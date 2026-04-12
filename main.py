from flask import Flask, render_template, redirect, request, jsonify
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
import json
import zipfile, tempfile
import os
import argon2
from instances import *
import jinja2

class NoKeysException(RuntimeError): pass
class ConfigError(TypeError): pass

from sqlalchemy import create_engine, text
from sqlalchemy.orm import declarative_base
engine=create_engine(os.environ['POSTGRES_CONNECT_URI'])

Base = declarative_base()
url_to_hostname=re.compile(r'https://(.*)')

class UserEntry(Base):
    from sqlalchemy import Column, String
    __tablename__ = "users"

    id = Column(String, primary_key=True)
    password = Column(String, nullable=False) # not the password, the hex hash

class IMUserEntry(Base):
    from sqlalchemy import Column, String
    __tablename__ = "im_users"

    id = Column(String, primary_key=True)
    password = Column(String, nullable=False) # not the password, the hex hash

class PlayerEntry(Base):
    from sqlalchemy import Column, BigInteger, String, ForeignKey, PrimaryKeyConstraint, ForeignKeyConstraint
    __tablename__ = "players"

    name = Column(String, nullable=False)
    username = Column(String, ForeignKey("im_users.id"), nullable=False)
    instance = Column(String, nullable=False)
    uploaddir = Column(String, nullable=False)
    ownerID = Column(String, ForeignKey("users.id"), nullable=False)
    player_key = Column(BigInteger, nullable=False)
    observer_key = Column(BigInteger, nullable=False)
    testserver = Column(String, nullable=False)

    # the following columns are unused, and are needed for foreign key only
    pk_instance = Column(String)
    ok_instance = Column(String)
    pk_username = Column(String)
    ok_username = Column(String)
    __table_args__ = (
        PrimaryKeyConstraint("username","instance","name"),
        ForeignKeyConstraint(['player_key','pk_instance','pk_username'],["player_keys.player_key","player_keys.instance","player_keys.username"]),
        ForeignKeyConstraint(['observer_key','ok_instance','ok_username'],["observer_keys.observer_key","observer_keys.instance","observer_keys.username" ]),
    )

class PlayerKeys(Base):
    from sqlalchemy import Column, String, BigInteger, Boolean, text, PrimaryKeyConstraint, ForeignKey
    __tablename__ = "player_keys"

    username=Column(String, ForeignKey("im_users.id"), nullable=False)
    instance=Column(String, nullable=False)
    player_key=Column(BigInteger, nullable=False)
    used=Column(Boolean, nullable=False, server_default=text("FALSE"))
    __table_args__ = (
        PrimaryKeyConstraint("username","instance","player_key"),
    )

class ObserverKeys(Base):
    from sqlalchemy import Column, String, BigInteger, Boolean, text, PrimaryKeyConstraint, ForeignKey
    __tablename__ = "observer_keys"

    username=Column(String, ForeignKey("im_users.id"), nullable=False)
    instance=Column(String, nullable=False)
    observer_key=Column(BigInteger, nullable=False)
    used=Column(Boolean, nullable=False, server_default=text("FALSE"))
    __table_args__ = (
        PrimaryKeyConstraint("username","instance","observer_key"),
    )

def check_user(id,password):
    with engine.connect() as conn:
        result = conn.execute(
            text("SELECT password FROM im_users WHERE id = :id"),
            {"id": id}
        )
        password_hash=result.scalar()
        if not password_hash: return False
        try:
            return argon2.PasswordHasher().verify(password_hash,password)
        except argon2.exceptions.VerifyMismatchError:
            return False

# wrapper for login required routes
def login_view(route,*args,**kwargs):
    def wrapper(view):
        login_manager.login_view = route
        return app.route(route,*args,**kwargs)(view)
    return wrapper

Base.metadata.create_all(engine)

config_templates=jinja2.Environment(loader=jinja2.FileSystemLoader('config-templates'))
def format_config_template(file, **kwargs):
    return config_templates.get_template(file).render(**kwargs)

app = Flask(__name__)
app.secret_key = os.environ['SECRET_KEY']

login_manager = LoginManager()
login_manager.init_app(app)

class User(UserMixin):
    def __init__(self, id):
        self.id = id

@login_manager.user_loader
def load_user(user_id):
    return User(user_id)


def render_template_with_user(template_name, **kwargs):
    return render_template(template_name, username=getattr(current_user, 'id', None), **kwargs)

@app.route("/")
@login_required
def home():
    # Render index.html from the templates folder
    return render_template_with_user("index.html", instances=get_active_instances(current_user.id), frontend_url=os.environ['fe_host'])

@app.route("/details")
@login_required
def details():
    try:
        instance=request.args['instance']
    except KeyError:
        return "Instance required",400
    instances=get_active_instances(current_user.id)
    if instance not in instances:
        return "Instance not found",404
    with engine.connect() as conn:
        player_rows=conn.execute(text("SELECT name FROM players WHERE instance=:instance"),{"instance":instance}).fetchall()
        players=[player_row[0] for player_row in player_rows]
    # Render index.html from the templates folder
    if not os.path.isdir(instances[instance]['config_dir']):
        return render_template_with_user("details.html", instance=instance, instances=instances,players=players,nocorrupt=False,corrupt_error="cannot find config dir for instance, please recreate this instance")
    return render_template_with_user("details.html", instance=instance, instances=instances,players=players,nocorrupt=True)

@app.route("/new",methods=['GET'])
@login_required
def new_instance():
    # Render new_instance.html from templates
    return render_template_with_user("new_instance.html")

@app.route("/players/new",methods=['GET'])
@login_required
def new_player():
    # Render new_instance.html from templates
    return render_template_with_user("new_player.html", instance=request.args.get("instance"))

@app.route("/new",methods=['POST'])
@login_required
def api_new_instance():
    try:
        config_zip=request.files.get('config-zip')
        config_dir=tempfile.mkdtemp()
        import shutil
        for mixin_file in os.listdir('mixin-config'):
            shutil.copy(os.path.join('mixin-config',mixin_file),config_dir)
        with zipfile.ZipFile(config_zip, 'r') as zip_device:
            safe_extract(zip_device, config_dir)
        keyfile=open(os.path.join(config_dir,'observer_keys.json'),'r')
        observer_keys=json.load(keyfile)
        keyfile.close()
        name=request.form.get('name')
        with engine.connect() as conn:
            conn.begin()
            for key in observer_keys:
                if not isinstance(key,int): raise ConfigError("observer_keys.json: non-integer found")
                conn.execute(text('INSERT INTO observer_keys (username,instance,observer_key) VALUES (:username,:instance,:observer_key)'),{'username':current_user.id,'instance':name,'observer_key':key})
            conn.execute(text('UPDATE observer_keys SET used=TRUE WHERE instance=:instance AND observer_key=:observer_key'),{'instance':name,'observer_key':observer_keys[0]})
            keyfile=open(os.path.join(config_dir,'player_keys.json'),'r')
            player_keys=json.load(keyfile)
            keyfile.close()
            for key in player_keys:
                if not isinstance(key,int): raise ConfigError("player_keys.json: non-integer found")
                conn.execute(text('INSERT INTO player_keys(username,instance,player_key) VALUES (:username,:instance,:player_key)'),{'username':current_user.id,'instance':name,'player_key':key})
            conn.commit()
        spawn_new_instance(current_user.id,name,config_dir,observer_keys[0],start=request.form.get('autoStart'))
    except ConflictException:
        return render_template_with_user("new_instance.html",error="Docker container conflict. Please choose another name")
    except ConfigError as e:
        return render_template_with_user("new_instance.html",error=f"Configuration error: {e.args[0]}")
    return redirect("/")

@app.route("/players/new",methods=['POST'])
@login_required
def api_new_player():
    instances=get_active_instances(current_user.id)
    try:
        name=request.form.get('name')
        instance=request.form.get('instance')
        userID=containerName=f"{current_user.id}-{instance}-{name}"
        if not (name and instance):
            return render_template_with_user("new_player.html",instance=instance,error="Player name and instance name required")
        if instance not in instances:
            return render_template_with_user("new_player.html",instance=instance,error="Instance not found")
        uploaddir=f"/tmp/{containerName}"
        os.makedirs(uploaddir)
        import secrets,base64
        credentials={"userID":userID,"password":base64.b64encode(secrets.token_bytes(8)).decode()}
        with engine.connect() as conn:
            conn.begin()
            if conn.execute(text("SELECT * FROM players WHERE name=:name AND instance=:instance"),{"name":name,"instance":instance}).fetchone(): raise ConflictException
            player_key=conn.execute(text("SELECT player_key FROM player_keys WHERE instance=:instance AND username=:username AND used=FALSE"),{"instance":instance,"username":current_user.id}).fetchone()
            if player_key:
                player_key=player_key[0]
                conn.execute(text("UPDATE player_keys SET used=true WHERE instance=:instance AND username=:username AND player_key=:player_key"),{"instance":instance,"username":current_user.id,"player_key":player_key})
            else:
                raise NoKeysException()
            observer_key=conn.execute(text("SELECT observer_key FROM observer_keys WHERE instance=:instance AND username=:username AND used=FALSE"),{"instance":instance,"username":current_user.id}).fetchone()
            if observer_key:
                observer_key=observer_key[0]
                conn.execute(text("UPDATE observer_keys SET used=true WHERE instance=:instance AND username=:username AND observer_key=:observer_key"),{"instance":instance,"username":current_user.id,"observer_key":observer_key})
            else:
                raise NoKeysException()
            conn.execute(text("INSERT INTO users (id, password) VALUES (:id,:password)"),{"id":credentials["userID"],"password":argon2.PasswordHasher().hash(credentials["password"])})
            conn.execute(text("INSERT INTO players (username,name,instance,uploaddir,\"ownerID\",testserver,player_key,observer_key) VALUES (:username,:name,:instance,:uploaddir,:owner,:testserver,:player_key,:observer_key)"),{"username":current_user.id,"name":name,"instance":instance,"uploaddir":uploaddir,"owner":credentials["userID"],"testserver":f'{name}-{instance}',"player_key":player_key,"observer_key":observer_key})
            conn.commit()
        with zipfile.ZipFile(os.path.join(uploaddir,"testpack.zip"),mode='w') as configpack:
            with configpack.open('server_config.json','w') as scfile:
                scfile.write(format_config_template('server_config.json',hostname=f'{name}-{instance}-mb.{os.environ["BASE_DOMAIN"]}').encode())
            with configpack.open('player_config.json','w') as pcfile:
                pcfile.write(format_config_template('player_config.json',player_name=f'{name}',player_key=player_key,observer_name=f'{name}:observer',observer_key=observer_key).encode())
        with zipfile.ZipFile(os.path.join(uploaddir,"comppack.zip"),mode='w') as configpack:
            with configpack.open('server_config.json','w') as scfile:
                scfile.write(format_config_template('server_config.json',hostname=f'{instance}-mb.{os.environ["BASE_DOMAIN"]}').encode())
            with configpack.open('player_config.json','w') as pcfile:
                pcfile.write(format_config_template('player_config.json',player_name=f'{name}',player_key=player_key,observer_name=f'{name}:observer',observer_key=observer_key).encode())
        os.makedirs (uploaddir,exist_ok=True)
    except ConflictException:
        return render_template_with_user("new_player.html",instance=instance,error="Player name conflict. Please choose another name")
    except NoKeysException:
        return render_template_with_user("new_player.html",instance=instance,error="No keys left, the instance cannot fit any more players")
    with engine.connect() as conn:
        player_rows=conn.execute(text("SELECT name FROM players WHERE instance=:instance"),{"instance":instance}).fetchall()
        players=[player_row[0] for player_row in player_rows]
    spawn_player(current_user.id,name,instance,instances)
    return render_template_with_user("details.html",instance=instance,instances=instances,players=players,showcred_player=name,showcred_creds=credentials,nocorrupt=True)


@app.route("/favicon.ico")
def favicon():
    return redirect("/static/favicon.ico")

@app.route("/stop",methods=['POST'])
@login_required
def api_stop_instance():
    try:
        instance=request.args['instance']
    except KeyError:
        return jsonify({"error":"instance name required"}),500
    if instance not in get_active_instances(current_user.id):
        return jsonify({"error":"instance not found"}),404
    if (error:=stop_instance(current_user.id,instance))['success']:
        return "",204
    else:
        return jsonify({"error":"failed to stop","rawError":error['rawError']}),500
@app.route("/delete",methods=['DELETE'])
@login_required
def api_delete_instance():
    try:
        instance=request.args['instance']
    except KeyError:
        return jsonify({"error":"instance name required"}),500
    if instance not in (instances:=get_active_instances(current_user.id)):
        return jsonify({"error":"instance not found"}),404
    container=instances[instance]
    if (error:=delete_instance(current_user.id,instance))['success']:
        import shutil
        if container['config_dir'] and os.path.isdir(container['config_dir']):
            shutil.rmtree(container['config_dir'])
        with engine.connect() as conn:
            conn.begin()
            ownerIDs=[]
            player_keys=[]
            observer_keys=[]
            for row in conn.execute(text("SELECT name, instance, uploaddir, \"ownerID\", player_key, observer_key FROM players WHERE instance=:instance AND username=:username"),{"instance":instance,"username":current_user.id}).fetchall():
                uploaddir=row[2]
                shutil.rmtree(uploaddir)
                delete_player(current_user.id,row[0],row[1])
                ownerIDs.append(row[3])
                player_keys.append(row[4])
                observer_keys.append(row[5])
            conn.execute(text("DELETE FROM players WHERE instance=:instance AND username=:username"),{"instance":instance,"username":current_user.id})
            for ownerID in ownerIDs:
                conn.execute(text("DELETE FROM users WHERE id=:ownerID"),{"ownerID":ownerID})
            # reclaim the keys
            conn.execute(text("DELETE FROM player_keys WHERE instance=:instance AND username=:username"),{"instance":instance,"username":current_user.id})
            conn.execute(text("DELETE FROM observer_keys WHERE instance=:instance AND username=:username"),{"instance":instance,"username":current_user.id})
            conn.commit()

        return "",204
    else:
        return jsonify({"error":"failed to delete","rawError":error['rawError']}),500
@app.route("/players/delete",methods=['DELETE'])
@login_required
def api_delete_player():
    with engine.connect() as conn:
        conn.begin()
        try:
            instance=request.args['instance']
            player=request.args['player']
        except KeyError:
            return jsonify({"error":"instance and player name required"}),500
        row=conn.execute(text("SELECT uploaddir,\"ownerID\",player_key,observer_key FROM players WHERE name=:name AND instance=:instance"),{"instance":instance,"name":player}).fetchone() # fetch one, it's unique
        if not row:
            return jsonify({"error":"player not found on instance"}),404
        if not (error:=delete_player(current_user.id,player,instance))['success']:
            return jsonify({"error":"failed to delete test server","rawError":error['rawError']}),500
        import shutil
        shutil.rmtree(row[0])
        conn.execute(text("DELETE FROM players WHERE name=:name AND username=:username AND instance=:instance"),{"instance":instance,"name":player,"username":current_user.id})
        conn.execute(text("DELETE FROM users WHERE id=:id"),{"id":row[1]})
        conn.execute(text("UPDATE player_keys SET used=FALSE WHERE instance=:instance AND username=:username AND player_key=:player_key"),{"instance":instance,"username":current_user.id,"player_key":row[2]})
        conn.execute(text("UPDATE observer_keys SET used=FALSE WHERE instance=:instance AND username=:username AND observer_key=:observer_key"),{"instance":instance,"username":current_user.id,"observer_key":row[3]})
        conn.commit()
        return "",204
@app.route("/start",methods=['POST'])
@login_required
def api_start_instance():
    try:
        instance=request.args['instance']
    except KeyError:
        return jsonify({"error":"instance name required"}),500
    if instance not in get_active_instances(current_user.id):
        return jsonify({"error":"instance not found"}),404
    if (error:=start_instance(current_user.id,instance))['success']:
        return "",204
    else:
        return jsonify({"error":"failed to start","rawError":error['rawError']}),500

@app.route("/login",methods=['GET'])
@login_view('/login')
def login():
    if current_user.is_authenticated:
        return redirect(request.args.get('next') or '/')
    return render_template("login.html")

@app.route("/login", methods=['POST'])
def login_post():
    username = request.form.get('userID')
    password = request.form.get('password')
    if check_user(username,password):
        login_user(User(id=username))
        return redirect(request.args.get('next') or '/')
    else:
        return render_template("login.html",error="Login incorrect")

@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect("/login")

@app.route("/healthcheck",methods=['GET'])
def healthcheck(): return "",204

setup_networking()
