from flask import Flask, render_template, redirect, request, jsonify, session
from flask.sessions import SecureCookieSessionInterface
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from datetime import timedelta
import json
import zipfile, tempfile
import os
import argon2
from instances import *
import jinja2
from urllib.parse import urlparse

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
    from sqlalchemy import Column, String, BigInteger
    __tablename__ = "im_users"

    id = Column(String, primary_key=True)
    password = Column(String, nullable=False) # not the password, the hex hash
    passwordChangeNonce = Column(BigInteger, nullable=False)

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
    instance_observer_key = Column(BigInteger, nullable=False)
    instance_config_dir = Column(String, nullable=False)
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

class InstanceShares(Base):
    from sqlalchemy import Column, String, ForeignKey, PrimaryKeyConstraint
    __tablename__ = "shared_instances"

    share_destination=Column(String, ForeignKey("im_users.id"), nullable=False)
    share_source=Column(String, ForeignKey("im_users.id"), nullable=False)
    instance=Column(String, nullable=False)
    __table_args__ = (
        PrimaryKeyConstraint("share_destination","share_source","instance"),
    )

class VirtualInstanceEntry(Base):
    from sqlalchemy import Column, String, BigInteger, text, PrimaryKeyConstraint, ForeignKey, ForeignKeyConstraint
    __tablename__ = "virtual_instances"

    username=Column(String, ForeignKey("im_users.id"), nullable=False)
    instance=Column(String, nullable=False)
    observer_key=Column(BigInteger, nullable=False)
    url=Column(String, nullable=False)
    config_dir=Column(String, nullable=False)
    # not needed, but makes foreign key constraints work because of the composite key
    ok_instance=Column(String, nullable=False)
    ok_username=Column(String, nullable=False)
    __table_args__ = (
        PrimaryKeyConstraint("username","instance"),
        ForeignKeyConstraint(['observer_key','ok_instance','ok_username'],["observer_keys.observer_key","observer_keys.instance","observer_keys.username" ]),
    )

class ContainerInstanceEntry(Base):
    from sqlalchemy import Column, String, BigInteger, text, PrimaryKeyConstraint, ForeignKey, ForeignKeyConstraint
    __tablename__ = "container_instances"

    username=Column(String, ForeignKey("im_users.id"), nullable=False)
    instance=Column(String, nullable=False)
    config_dir=Column(String, nullable=False)
    observer_key=Column(BigInteger, nullable=False)
    url=Column(String, nullable=False)
    # not needed, but makes foreign key constraints work because of the composite key
    ok_instance=Column(String, nullable=False)
    ok_username=Column(String, nullable=False)
    __table_args__ = (
        PrimaryKeyConstraint("username","instance"),
        ForeignKeyConstraint(['observer_key','ok_instance','ok_username'],["observer_keys.observer_key","observer_keys.instance","observer_keys.username" ]),
    )

Base.metadata.create_all(engine)

import redis
_redis_pool = None

def get_redis_client():
    """
    Lazy initialization. This guarantees the Connection Pool is spawned
    separately inside the memory space of whichever Gunicorn worker calls it.
    """
    global _redis_pool
    if _redis_pool is None:
        # max_connections limits the capacity PER WORKER
        _redis_pool = redis.ConnectionPool.from_url(
            os.environ.get("REDIS_CONNECT_URI"),
            decode_responses=True,
            max_connections=10
        )
    return redis.Redis(connection_pool=_redis_pool)

def get_cookie_expiry_timestamp(raw_cookie_string: str) -> int:
    """
    Parses a raw classic Flask session cookie, extracts its creation time,
    and returns the absolute Unix epoch timestamp of its exact expiration.
    """
    session_interface = SecureCookieSessionInterface()
    serializer = session_interface.get_signing_serializer(app)

    # Get your configured session duration in total seconds (e.g., 604800)
    max_age_seconds = int(app.permanent_session_lifetime.total_seconds())

    # loads_with_timestamp decrypts and extracts the birth datetime of the cookie
    _, created_at_datetime = serializer.loads(
        raw_cookie_string,
        max_age=max_age_seconds,
        return_timestamp=True  # ◄── This forces the return of the creation stamp
    )

    # Convert the creation datetime to a standard Unix Epoch Integer
    created_at_epoch = int(created_at_datetime.timestamp())

    # Absolute Expiry = Born Timestamp + Allowed Lifespan
    absolute_expiry_epoch = created_at_epoch + max_age_seconds
    return absolute_expiry_epoch


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

config_templates=jinja2.Environment(loader=jinja2.FileSystemLoader('config-templates'))
def format_config_template(file, **kwargs):
    return config_templates.get_template(file).render(**kwargs)

app = Flask(__name__)
app.secret_key = os.environ['SECRET_KEY']
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=5)

login_manager = LoginManager()
login_manager.init_app(app)

class User(UserMixin):
    def __init__(self, id, session_id, nonce):
        self.id = id
        self.session_id = session_id
        self.nonce = nonce

    def get_id(self):
        return self.id+'-'+str(self.session_id)+'-'+str(self.nonce)

@app.before_request
def force_permanent_session_lifecycle():
    """
    By setting this here, Flask is forced to treat the current browser cookie
    as long-lived, injecting the Max-Age and Expires headers automatically
    on the outgoing HTTP response.
    """
    session.permanent = True

@login_manager.user_loader
def load_user(user_id):
    components=user_id.split('-')
    user_id='-'.join(components[:-2])
    session_id=int(components[-2])
    nonce=int(components[-1])
    redis_client=get_redis_client()
    if redis_client.get(f'banned-session-{session_id}')=='':
        return None
    user_exists=redis_client.get(f'userstate.{user_id}')
    if user_exists is None:
        #check postgres
        with engine.connect() as conn:
            user_exists=bool(conn.execute(text("SELECT id FROM im_users WHERE id=:userid"),{"userid":user_id}).scalar())
            cookie=request.cookies.get('session')
            redis_client.set(f'userstate.{user_id}',str(user_exists),exat=get_cookie_expiry_timestamp(cookie))
    user_exists=user_exists=="True"
    if not user_exists:
      return None
    # cache the nonce
    if not (db_nonce:=redis_client.get(f'nonce.{user_id}')):
        with engine.connect() as conn:
            db_nonce=conn.execute(text("SELECT \"passwordChangeNonce\" FROM im_users WHERE id=:id"),{"id":user_id}).scalar()
            redis_client.set(f'nonce.{user_id}',db_nonce)
    db_nonce=int(db_nonce)
    if db_nonce!=nonce:
       return None # the password was changed on another device
    # this session is permitted
    return User(user_id,session_id,nonce)


def render_template_with_user(template_name, **kwargs):
    return render_template(template_name, username=getattr(current_user, 'id', None), **kwargs)

@app.route("/")
@login_required
def home():
    # Render index.html from the templates folder
    return render_template_with_user("index.html", instances=get_active_instances(current_user.id,engine), frontend_url=os.environ['fe_host'])

@app.route("/change_password")
@login_required
def change_password():
    return render_template_with_user("change_password.html")

@app.route("/details")
@login_required
def details():
    try:
        instance=request.args['instance']
    except KeyError:
        return "Instance required",400
    instances=get_active_instances(current_user.id,engine)
    if instance not in instances:
        return "Instance not found",404
    instance_raw=instance
    username, instance=parse_container_name(current_user.id,instance)
    with engine.connect() as conn:
        player_rows=conn.execute(text("SELECT name FROM players WHERE instance=:instance AND username=:username"),{"instance":instance,"username":username}).fetchall()
        players=[player_row[0] for player_row in player_rows]
    # Render index.html from the templates folder
    if not os.path.isdir(instances[instance_raw]['config_dir']):
        return render_template_with_user("details.html", instance=instance_raw, instances=instances,players=players,nocorrupt=False,corrupt_error="cannot find config dir for instance, please recreate this instance",frontend_url=os.environ['fe_host'])
    return render_template_with_user("details.html", instance=instance_raw, instances=instances,players=players,nocorrupt=True,frontend_url=os.environ['fe_host'])

@app.route("/new",methods=['GET'])
@login_required
def new_instance():
    # Render new_instance.html from templates
    if request.args.get('type')=="virtual":
        return render_template_with_user("new_virtual_instance.html")
    return render_template_with_user("new_instance.html")

@app.route("/players/new",methods=['GET'])
@login_required
def new_player():
    # Render new_instance.html from templates
    return render_template_with_user("new_player.html", instance=request.args.get("instance"))

@app.route("/new",methods=['POST'])
@login_required
def api_new_instance():
    def error_cleanup():
        shutil.rmtree(config_dir)
    try:
        config_zip=request.files.get('config-zip')
        config_dir=tempfile.mkdtemp()
        import shutil
        for mixin_file in os.listdir('mixin-config'):
            shutil.copy(os.path.join('mixin-config',mixin_file),config_dir)
        with zipfile.ZipFile(config_zip, 'r') as zip_device:
            safe_extract(zip_device, config_dir)
        keypath=os.path.join(config_dir,'observer_keys.json')
        if not os.path.isfile(keypath): raise ConfigError("required files not found: observer_keys.json")
        keyfile=open(keypath,'r')
        try:
            observer_keys=json.load(keyfile)
        except:
            raise ConfigError('observer_keys.json: invalid JSON')
        keyfile.close()
        name=request.form.get('name')
        type=request.form.get('type')
        error_template="new_instance.html" if type=="docker" else "new_virtual_instance.html" # to render errors
        if type=="docker":
            with engine.connect() as conn:
                conn.begin()
                for key in observer_keys:
                    if not isinstance(key,int): raise ConfigError("observer_keys.json: non-integer found")
                    conn.execute(text('INSERT INTO observer_keys (username,instance,observer_key) VALUES (:username,:instance,:observer_key)'),{'username':current_user.id,'instance':name,'observer_key':key})
                conn.execute(text('UPDATE observer_keys SET used=TRUE WHERE instance=:instance AND observer_key=:observer_key'),{'instance':name,'observer_key':observer_keys[0]})
                keypath=os.path.join(config_dir,'player_keys.json')
                if not os.path.isfile(keypath): raise ConfigError("required files not found: player_keys.json")
                keyfile=open(keypath,'r')
                try:
                    player_keys=json.load(keyfile)
                except:
                    raise ConfigError('player_keys.json: invalid JSON')
                keyfile.close()
                for key in player_keys:
                    if not isinstance(key,int): raise ConfigError("player_keys.json: non-integer found")
                    conn.execute(text('INSERT INTO player_keys(username,instance,player_key) VALUES (:username,:instance,:player_key)'),{'username':current_user.id,'instance':name,'player_key':key})
                conn.execute(text('INSERT INTO container_instances (username,instance,config_dir,observer_key,url,ok_instance,ok_username) VALUES (:username,:instance,:config_dir,:observer_key,:url,:instance,:username)'),{'username':current_user.id,'instance':name,'config_dir':config_dir,'observer_key':observer_keys[0],'url':f'https://{current_user.id}-{name}-mb.{os.environ["BASE_DOMAIN"]}'})
                autoStart=request.form.get('autoStart')
                if autoStart:
                    spawn_new_instance(current_user.id,name,config_dir,observer_keys[0])
                conn.commit()
        elif type=="virtual":
            url=request.form.get('url')
            if not url:
                raise ConfigError("URL is required for virtual instances")

            observer_key=int(request.form.get('primaryKey'))
            if observer_key in (None,""):
                raise ConfigError("Primary observer key is required for virtual instances")
            if observer_key not in observer_keys:
                raise ConfigError("Observer key is not valid")
            with engine.connect() as conn:
                conn.begin()
                keyfile=open(keypath,'r')
                try:
                    player_keys=json.load(keyfile)
                except:
                    raise ConfigError('player_keys.json: invalid JSON')
                for key in player_keys:
                    if not isinstance(key,int): raise ConfigError("player_keys.json: non-integer found")
                    conn.execute(text('INSERT INTO player_keys(username,instance,player_key) VALUES (:username,:instance,:player_key)'),{'username':current_user.id,'instance':name,'player_key':key})
                for key in observer_keys:
                    if not isinstance(key,int): raise ConfigError("observer_keys.json: non-integer found")
                    conn.execute(text('INSERT INTO observer_keys (username,instance,observer_key) VALUES (:username,:instance,:observer_key)'),{'username':current_user.id,'instance':name,'observer_key':key})
                conn.execute(text('UPDATE observer_keys SET used=TRUE WHERE instance=:instance AND observer_key=:observer_key'),{'instance':name,'observer_key':observer_key})
                conn.execute(text('INSERT INTO virtual_instances (username,instance,observer_key,url,config_dir,ok_instance,ok_username) VALUES (:username,:instance,:observer_key,:url,:config_dir,:ok_instance,:ok_username)'),{'username':current_user.id,'instance':name,'observer_key':observer_key,'url':url,'config_dir':config_dir,'ok_instance':name,'ok_username':current_user.id})
                conn.commit()
    except ConflictException:
        error_cleanup()
        return render_template_with_user(error_template,error="Docker container conflict. Please choose another name")
    except ConfigError as e:
        error_cleanup()
        return render_template_with_user(error_template,error=f"Configuration error: {e.args[0]}")
    except:
        error_cleanup()
        raise
    return redirect("/")

@app.route("/players/new",methods=['POST'])
@login_required
def api_new_player():
    instances=get_active_instances(current_user.id,engine)
    try:
        name=request.form.get('name')
        instance=request.form.get('instance')
        instance_raw=instance
        username, instance=parse_container_name(current_user.id,instance)
        userID=containerName=f"{username}-{instance}-{name}"
        if not (name and instance):
            return render_template_with_user("new_player.html",instance=instance_raw,error="Player name and instance name required")
        if instance not in instances:
            return render_template_with_user("new_player.html",instance=instance_raw,error="Instance not found")
        uploaddir=f"/tmp/{containerName}"
        import secrets,base64
        credentials={"userID":userID,"password":base64.b64encode(secrets.token_bytes(8)).decode()}
        with engine.connect() as conn:
            conn.begin()
            if conn.execute(text("SELECT * FROM players WHERE name=:name AND instance=:instance AND username=:username"),{"name":name,"instance":instance,"username":username}).fetchone(): raise ConflictException
            player_key=conn.execute(text("SELECT player_key FROM player_keys WHERE instance=:instance AND username=:username AND used=FALSE"),{"instance":instance,"username":username}).fetchone()
            if player_key:
                player_key=player_key[0]
                conn.execute(text("UPDATE player_keys SET used=true WHERE instance=:instance AND username=:username AND player_key=:player_key"),{"instance":instance,"username":username,"player_key":player_key})
            else:
                raise NoKeysException()
            observer_key=conn.execute(text("SELECT observer_key FROM observer_keys WHERE instance=:instance AND username=:username AND used=FALSE"),{"instance":instance,"username":username}).fetchone()
            if observer_key:
                observer_key=observer_key[0]
                conn.execute(text("UPDATE observer_keys SET used=true WHERE instance=:instance AND username=:username AND observer_key=:observer_key"),{"instance":instance,"username":username,"observer_key":observer_key})
            else:
                raise NoKeysException()
            conn.execute(text("INSERT INTO users (id, password) VALUES (:id,:password)"),{"id":credentials["userID"],"password":argon2.PasswordHasher().hash(credentials["password"])})
            conn.execute(text("INSERT INTO players (username,name,instance,uploaddir,\"ownerID\",testserver,player_key,observer_key,instance_observer_key,instance_config_dir) VALUES (:username,:name,:instance,:uploaddir,:owner,:testserver,:player_key,:observer_key,:instance_observer_key,:instance_config_dir)"),{"username":username,"name":name,"instance":instance,"uploaddir":uploaddir,"owner":credentials["userID"],"testserver":f'{name}-{instance}',"player_key":player_key,"observer_key":observer_key,"instance_observer_key":instances[instance]['observer_key'],"instance_config_dir":instances[instance]['config_dir']})
            conn.commit()
        os.makedirs (uploaddir,exist_ok=True)
        with zipfile.ZipFile(os.path.join(uploaddir,"testpack.zip"),mode='w') as configpack:
            with configpack.open('server_config.json','w') as scfile:
                scfile.write(format_config_template('server_config.json',hostname=f'{userID}-mb.{os.environ["BASE_DOMAIN"]}').encode())
            with configpack.open('player_config.json','w') as pcfile:
                pcfile.write(format_config_template('player_config.json',player_name=f'{name}',player_key=player_key,observer_name=f'{name}:observer',observer_key=observer_key).encode())
        with zipfile.ZipFile(os.path.join(uploaddir,"comppack.zip"),mode='w') as configpack:
            with configpack.open('server_config.json','w') as scfile:
                if instances[instance_raw]['type']=="docker":
                    scfile.write(format_config_template('server_config.json',hostname=f'{username}-{instance}-mb.{os.environ["BASE_DOMAIN"]}').encode())
                else:
                    url=urlparse(instances[instance_raw]['url'])
                    scfile.write(format_config_template('server_config.json',hostname=url.hostname,port=url.port,security=url.scheme=="https").encode())
            with configpack.open('player_config.json','w') as pcfile:
              pcfile.write(format_config_template('player_config.json',player_name=f'{name}',player_key=player_key,observer_name=f'{name}:observer',observer_key=observer_key).encode())
    except ConflictException:
        return render_template_with_user("new_player.html",instance=instance_raw,error="Player name conflict. Please choose another name")
    except NoKeysException:
        return render_template_with_user("new_player.html",instance=instance_raw,error="No keys left, the instance cannot fit any more players")
    with engine.connect() as conn:
        player_rows=conn.execute(text("SELECT name FROM players WHERE instance=:instance AND username=:username"),{"instance":instance,"username":username}).fetchall()
        players=[player_row[0] for player_row in player_rows]
    return render_template_with_user("details.html",instance=instance_raw,instances=instances,players=players,showcred_player=name,showcred_creds=credentials,nocorrupt=True)


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
    if instance not in get_active_instances(current_user.id,engine):
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
    if instance not in (instances:=get_active_instances(current_user.id,engine)):
        return jsonify({"error":"instance not found"}),404
    if '/' in instance:
        return jsonify({"error":"only the owner can delete an instance"}),403
    with engine.connect() as conn:
        conn.begin()
        import shutil
        config_dir=instances[instance]['config_dir']
        if config_dir and os.path.isdir(config_dir):
            shutil.rmtree(config_dir)
        conn.execute(text("DELETE FROM shared_instances WHERE instance=:instance AND share_source=:username"),{"username":current_user.id,"instance":instance})
        # remove a virtual instance. Delete only the database entries, we don't manage the instance itself so we can't do anything to it
        if instances[instance]['type']!="docker":
            conn.execute(text("DELETE FROM virtual_instances WHERE username=:username AND instance=:instance"),{"username":current_user.id,"instance":instance})
            conn.execute(text("DELETE FROM observer_keys WHERE username=:username AND instance=:instance"),{"username":current_user.id,"instance":instance})
            conn.commit()
            return "",204
        username, instance=parse_container_name(current_user.id,instance)
        conn.execute(text("DELETE FROM container_instances WHERE instance=:instance AND username=:username"),{"instance":instance,"username":username})
        ownerIDs=[]
        player_keys=[]
        observer_keys=[]
        for row in conn.execute(text("SELECT name, instance, uploaddir, \"ownerID\", player_key, observer_key FROM players WHERE instance=:instance AND username=:username"),{"instance":instance,"username":username}).fetchall():
            uploaddir=row[2]
            shutil.rmtree(uploaddir)
            if is_player_testserver_running(username,row[0],instance):
                delete_player(username,row[0],instance)
            ownerIDs.append(row[3])
            player_keys.append(row[4])
            observer_keys.append(row[5])
        conn.execute(text("DELETE FROM players WHERE instance=:instance AND username=:username"),{"instance":instance,"username":username})
        for ownerID in ownerIDs:
            conn.execute(text("DELETE FROM users WHERE id=:ownerID"),{"ownerID":ownerID})
        # reclaim the keys
        conn.execute(text("DELETE FROM player_keys WHERE instance=:instance AND username=:username"),{"instance":instance,"username":username})
        conn.execute(text("DELETE FROM observer_keys WHERE instance=:instance AND username=:username"),{"instance":instance,"username":username})
        conn.commit()
    return "",204
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
        instance_raw=instance
        username,instance=parse_container_name(current_user.id,instance)
        row=conn.execute(text("SELECT uploaddir,\"ownerID\",player_key,observer_key FROM players WHERE name=:name AND instance=:instance AND username=:username"),{"instance":instance,"name":player,"username":username}).fetchone() # fetch one, it's unique
        if not row:
            return jsonify({"error":"player not found on instance"}),404
        if is_player_testserver_running(username,player,instance):
            delete_player(username,player,instance)
        import shutil
        shutil.rmtree(row[0])
        conn.execute(text("DELETE FROM players WHERE name=:name AND username=:username AND instance=:instance"),{"instance":instance,"name":player,"username":username})
        conn.execute(text("DELETE FROM users WHERE id=:id"),{"id":row[1]})
        get_redis_client().delete(f"userstate.competition-manager.{row[1]}")
        conn.execute(text("UPDATE player_keys SET used=FALSE WHERE instance=:instance AND username=:username AND player_key=:player_key"),{"instance":instance,"username":username,"player_key":row[2]})
        conn.execute(text("UPDATE observer_keys SET used=FALSE WHERE instance=:instance AND username=:username AND observer_key=:observer_key"),{"instance":instance,"username":username,"observer_key":row[3]})
        conn.commit()
        return "",204
@app.route("/start",methods=['POST'])
@login_required
def api_start_instance():
    try:
        instance=request.args['instance']
    except KeyError:
        return jsonify({"error":"instance name required"}),500
    if instance not in (instances:=get_active_instances(current_user.id,engine)):
        return jsonify({"error":"instance not found"}),404
    raw_instance=instance
    username, instance=parse_container_name(current_user.id,instance)
    if (error:=spawn_new_instance(username,instance,instances[raw_instance]['config_dir'],instances[raw_instance]['observer_key']))['success']:
        return "",204
    else:
        return jsonify({"error":"failed to start","rawError":error['rawError']}),500

@app.route('/sharing',methods=['GET'])
@login_required
def sharing_settings():
    instance=request.args.get('instance')
    with engine.connect() as conn:
        share_destinations=[row[0] for row in conn.execute(text("SELECT share_destination FROM shared_instances WHERE share_source=:source AND instance=:instance"),{"source":current_user.id,"instance":instance}).fetchall()]
    return render_template_with_user("sharing_settings.html",instances=get_active_instances(current_user.id,engine), instance=instance, share_destinations=share_destinations)

@app.route('/sharing/share',methods=['GET'])
@login_required
def share():
    instance=request.args.get('instance')
    return render_template_with_user("share.html",instances=get_active_instances(current_user.id,engine), instance=instance)

@app.route('/sharing/delete',methods=['DELETE'])
@login_required
def api_delete():
    instance=request.args.get('instance')
    destination=request.args.get('destination')
    username,instance=parse_container_name(current_user.id,instance)
    if username!=current_user.id:
        return jsonify({"error":"can only delete shares for instances you own"}),403
    with engine.connect() as conn:
        conn.begin()
        if not conn.execute(text("SELECT id FROM im_users WHERE id=:id"),{"id":destination}).fetchone():
            return jsonify({"error":f"destination user {destination} does not exist"}),404
        if not conn.execute(text("SELECT * FROM shared_instances WHERE share_source=:source AND share_destination=:destination AND instance=:instance"),{"source":current_user.id,"destination":destination,"instance":instance}).fetchone():
            return jsonify({"error":f"share to {destination} does not exist"}),404
        conn.execute(text("DELETE FROM shared_instances WHERE share_source=:source AND share_destination=:destination AND instance=:instance"),{"source":current_user.id,"destination":destination,"instance":instance})
        conn.commit()
    return "",204

@app.route('/sharing/share',methods=['POST'])
@login_required
def api_share():
    instance=request.form.get('instance')
    raw_instance=instance
    username,instance=parse_container_name(current_user.id,instance)
    if username!=current_user.id:
        return jsonify({"error":"can only share instances you own"}),403
    destinations=request.form.get('destinations')
    if not destinations:
        return jsonify({"error":"destinations are required"}),400
    destinations=destinations.splitlines()
    with engine.connect() as conn:
        conn.begin()
        for destination in destinations:
            destination=destination.strip()
            if not destination: continue
            if not conn.execute(text("SELECT id FROM im_users WHERE id=:id"),{"id":destination}).fetchone():
                return jsonify({"error":f"destination user {destination} does not exist"}),404
            if conn.execute(text("SELECT * FROM shared_instances WHERE share_source=:source AND share_destination=:destination AND instance=:instance"),{"source":current_user.id,"destination":destination,"instance":instance}).fetchone():
                return jsonify({"error":f"instance already shared with {destination}"}),409
            conn.execute(text("INSERT INTO shared_instances (share_source,share_destination,instance) VALUES (:source,:destination,:instance)"),{"source":current_user.id,"destination":destination,"instance":instance})
        conn.commit()
    return redirect(f"/sharing?instance={raw_instance}")

@app.route("/login",methods=['GET'])
@login_view('/login')
def login():
    if current_user.is_authenticated:
        next=request.args.get('next')
        if next and next.startswith('/'):
            return redirect(next)
        else:
            return redirect('/')
    return render_template("login.html")

@app.route("/login", methods=['POST'])
def login_post():
    username = request.form.get('userID')
    password = request.form.get('password')
    if check_user(username,password):
        import secrets
        redis_client=get_redis_client()
        if nonce:=redis_client.get(f'nonce.{username}'):
            nonce=int(nonce)
        else:
            with engine.connect() as conn:
                nonce=conn.execute(text("SELECT \"passwordChangeNonce\" FROM im_users WHERE id=:id"),{"id":username}).scalar()
                redis_client.set(f'nonce.{username}',nonce)
        login_user(User(id=username,session_id=secrets.randbits(128),nonce=nonce))
        next=request.args.get('next')
        if next and next.startswith('/'):
            return redirect(next)
        else:
            return redirect('/')
    else:
        return render_template("login.html",error="Login incorrect")

@app.route("/change_password", methods=['POST'])
@login_required
def change_password_post():
    old_password=request.form.get('current')
    new_password=request.form.get('new')
    confirm_password=request.form.get('confirm')
    if not check_user(current_user.id,old_password):
        return render_template_with_user("change_password.html",error="Current password incorrect")
    if new_password!=confirm_password:
        return render_template_with_user("change_password.html",error="New password and confirmation do not match")
    with engine.connect() as conn:
        import secrets
        nonce=secrets.randbits(63)
        conn.execute(text("UPDATE im_users SET password=:password,\"passwordChangeNonce\"=:nonce WHERE id=:id"),{"password":argon2.PasswordHasher().hash(new_password),"id":current_user.id,"nonce":nonce})
        conn.commit()
    get_redis_client().set(f'nonce.{current_user.id}',nonce)
    login_user(User(id=current_user.id,session_id=current_user.session_id,nonce=nonce))
    return render_template_with_user("cp_success.html")

@app.route("/logout")
@login_required
def logout():
    cookie=request.cookies.get('session')
    get_redis_client().set(f'banned-session-{current_user.session_id}','',exat=get_cookie_expiry_timestamp(cookie))
    logout_user()
    return redirect("/login")

@app.route("/healthcheck",methods=['GET'])
def healthcheck(): return "",204

setup_networking()
