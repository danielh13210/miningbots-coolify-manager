from flask import Flask, render_template, redirect, request, jsonify
import json
import zipfile, tempfile
import os
import re
import argon2
from instances import *

from sqlalchemy import create_engine, text
from sqlalchemy.orm import declarative_base
engine=create_engine(os.environ['POSTGRES_CONNECT_URI'])

Base = declarative_base()

class UserEntry(Base):
    from sqlalchemy import Column, String
    __tablename__ = "users"

    id = Column(String, primary_key=True)
    password = Column(String, nullable=False) # not the password, the hex hash

class PlayerEntry(Base):
    from sqlalchemy import Column, String, ForeignKey, PrimaryKeyConstraint
    __tablename__ = "players"

    name = Column(String, nullable=False)
    instance = Column(String, nullable=False)
    uploaddir = Column(String, nullable=False)
    ownerID = Column(String, ForeignKey("users.id"), nullable=False)
    testserver = Column(String, nullable=False)
    __table_args__ = (
        PrimaryKeyConstraint("instance","name"),
    )

Base.metadata.create_all(engine)

app = Flask(__name__)

@app.route("/")
def home():
    # Render index.html from the templates folder
    return render_template("index.html", instances=get_active_instances(), frontend_url=os.environ['fe_host'])

@app.route("/details")
def details():
    instance=request.args['instance']
    with engine.connect() as conn:
        player_rows=conn.execute(text("SELECT name FROM players WHERE instance=:instance"),{"instance":instance}).fetchall()
        players=[player_row[0] for player_row in player_rows]
    # Render index.html from the templates folder
    return render_template("details.html", instance=instance, instances=get_active_instances(),players=players)

@app.route("/new",methods=['GET'])
def new_instance():
    # Render new_instance.html from templates
    return render_template("new_instance.html")

@app.route("/players/new",methods=['GET'])
def new_player():
    # Render new_instance.html from templates
    return render_template("new_player.html",instance=request.args.get("instance"))

@app.route("/new",methods=['POST'])
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
        keys=json.load(keyfile)
        keyfile.close()
        name=request.form.get('name')
        spawn_new_instance(name,config_dir,keys[0],start=request.form.get('autoStart'))
    except ConflictException:
        return render_template("new_instance.html",error="Docker container conflict. Please choose another name")
    return redirect("/")

@app.route("/players/new",methods=['POST'])
def api_new_player():
    instances=get_active_instances()
    try:
        name=request.form.get('name')
        instance=request.form.get('instance')
        userID=containerName=f"{instance}-{name}"
        uploaddir=f"/tmp/{containerName}"
        import secrets,base64
        credentials={"userID":userID,"password":base64.b64encode(secrets.token_bytes(8)).decode()}
        with engine.connect() as conn:
            if conn.execute(text("SELECT * FROM players WHERE name=:name AND instance=:instance"),{"name":name,"instance":instance}).fetchone(): raise ConflictException
            conn.execute(text("INSERT INTO users (id, password) VALUES (:id,:password)"),{"id":credentials["userID"],"password":argon2.PasswordHasher().hash(credentials["password"])})
            conn.execute(text("INSERT INTO players (name,instance,uploaddir,\"ownerID\",testserver) VALUES (:name,:instance,:uploaddir,:owner,:testserver)"),{"name":name,"instance":instance,"uploaddir":uploaddir,"owner":credentials["userID"],"testserver":f'{name}-{instance}'})
            conn.commit()
        os.makedirs (uploaddir,exist_ok=True)
    except ConflictException:
        return render_template("new_player.html",instance=instance,error="Player name conflict. Please choose another name")
    with engine.connect() as conn:
        player_rows=conn.execute(text("SELECT name FROM players WHERE instance=:instance"),{"instance":instance}).fetchall()
        players=[player_row[0] for player_row in player_rows]
    spawn_player(name,instance,instances)
    return render_template("details.html",instance=instance,instances=instances,players=players,showcred_player=name,showcred_creds=credentials)


@app.route("/favicon.ico")
def favicon():
    return redirect("/static/favicon.ico")

@app.route("/stop",methods=['POST'])
def api_stop_instance():
    try:
        instance=request.args['instance']
    except KeyError:
        return jsonify({"error":"instance name required"}),500
    if instance not in get_active_instances():
        return jsonify({"error":"instance not found"}),404
    if (error:=stop_instance(instance))['success']:
        return "",204
    else:
        return jsonify({"error":"failed to stop","rawError":error['rawError']}),500
@app.route("/delete",methods=['DELETE'])
def api_delete_instance():
    try:
        instance=request.args['instance']
    except KeyError:
        return jsonify({"error":"instance name required"}),500
    if instance not in (instances:=get_active_instances()):
        return jsonify({"error":"instance not found"}),404
    container=instances[instance]
    if (error:=delete_instance(instance))['success']:
        if container['config_dir']:
            import shutil
            shutil.rmtree(container['config_dir'])
        with engine.connect() as conn:
            ownerIDs=[]
            for row in conn.execute(text("SELECT name, instance, uploaddir, \"ownerID\" FROM players WHERE instance=:instance"),{"instance":instance}).fetchall():
                uploaddir=row[2]
                shutil.rmtree(uploaddir)
                delete_player(row[0],row[1])
                ownerIDs.append(row[3])
            conn.execute(text("DELETE FROM players WHERE instance=:instance"),{"instance":instance})
            for ownerID in ownerIDs:
                conn.execute(text("DELETE FROM users WHERE id=:ownerID"),{"ownerID":ownerID})
            conn.commit()

        return "",204
    else:
        return jsonify({"error":"failed to delete","rawError":error['rawError']}),500
@app.route("/players/delete",methods=['DELETE'])
def api_delete_player():
    with engine.connect() as conn:
        try:
            instance=request.args['instance']
            player=request.args['player']
        except KeyError:
            return jsonify({"error":"instance and player name required"}),500
        row=conn.execute(text("SELECT uploaddir,\"ownerID\" FROM players WHERE name=:name AND instance=:instance"),{"instance":instance,"name":player}).fetchone() # fetch one, it's unique
        if not row:
            return jsonify({"error":"player not found on instance"}),404
        if not (error:=delete_player(player,instance))['success']:
            return jsonify({"error":"failed to delete test server","rawError":error['rawError']}),500
        import shutil
        shutil.rmtree(row[0])
        conn.execute(text("DELETE FROM players WHERE name=:name AND instance=:instance"),{"instance":instance,"name":player})
        conn.execute(text("DELETE FROM users WHERE id=:id"),{"id":row[1]})
        conn.commit()
        return "",204
@app.route("/start",methods=['POST'])
def api_start_instance():
    try:
        instance=request.args['instance']
    except KeyError:
        return jsonify({"error":"instance name required"}),500
    if instance not in get_active_instances():
        return jsonify({"error":"instance not found"}),404
    if (error:=start_instance(instance))['success']:
        return "",204
    else:
        return jsonify({"error":"failed to start","rawError":error['rawError']}),500
@app.route("/healthcheck",methods=['GET'])
def healthcheck(): return "",204

setup_networking()
