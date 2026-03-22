from flask import Flask, render_template, redirect, request, jsonify
import json
import zipfile, tarfile, stat, tempfile
import httpx
import os
import re

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
    __table_args__ = (
        PrimaryKeyConstraint("instance","name"),
    )

Base.metadata.create_all(engine)

app = Flask(__name__)

traefik_rule_matcher=re.compile(r'traefik\..*\.rule')
get_host=re.compile(r'Host\("(.*)"\)')

class ConflictException(ValueError): pass

def safe_extract(zip_file, target_dir):
    for info in zip_file.infolist():
        # Check for symlinks
        if stat.S_ISLNK(info.external_attr >> 16):
            continue

        # Build safe path
        extracted_path = os.path.join(target_dir, info.filename)
        abs_target = os.path.abspath(target_dir)
        abs_extracted = os.path.abspath(extracted_path)

        # Prevent path traversal
        if not abs_extracted.startswith(abs_target):
            continue

        zip_file.extract(info, target_dir)

def rebase_path_for_docker(path):
    from pathlib import Path

    old_base = Path("/tmp")
    new_base = Path("/data/mcm-data")
    original_file = Path(path)

    # Calculate the path relative to the old base, then join to the new base
    relative_path = original_file.relative_to(old_base)
    return (new_base / relative_path).as_posix()

def spawn_new_instance(name,config_dir,observer_key,start: bool=False):
    with httpx.Client(transport=httpx.HTTPTransport(uds="/var/run/docker.sock")) as client:
        payload={
            "Image": "miningbots-server",
            "Labels": {
                "miningbots-app-instance": "",
                "observer_key": str(observer_key),
                "traefik.enable": "true",
                f"traefik.http.routers.{name}-mb.rule": f'Host("{name}-mb.{os.environ['BASE_DOMAIN']}")',
                f"traefik.http.routers.{name}-mb.entrypoints": "https",
                f"traefik.http.routers.{name}-mb.tls": "true",
                f"traefik.http.routers.{name}-mb.tls.certresolver": "letsencrypt",
                f"traefik.http.services.{name}.loadbalancer.server.port": "9003",
                "configdir":config_dir
            },
            "HostConfig": {
                "NetworkMode": "mb-instances",
                    "Mounts": [
                        {
                            "Type": "bind",
                            "Source": rebase_path_for_docker(config_dir),
                            "Target": "/miningbots-server/config",
                            "ReadOnly": True
                        }
                    ]
            }
        }
        resp = client.post(f"http://localhost/containers/create?name={name}", json=payload)
        if resp.status_code!=201:
            if resp.status_code==409:
                raise ConflictException
            else:
                raise Exception(f"cannot create: http error {resp.status_code} {resp.json()}")
        if start:
            start_url = f"http://localhost/containers/{name}/start"
            resp = client.post(start_url)
            if resp.status_code!=204: raise Exception(f"cannot start: http error {resp.status_code} {resp.json()}")

def get_traefik_host(container):
    labels=container['Labels']
    for label in labels:
        if traefik_rule_matcher.match(label):
            rule=labels[label]
            if matches:=get_host.search(rule):
                return matches.group(1)
            else:
                raise KeyError
    raise KeyError
def get_observer_key(container):
    return container['Labels']['observer_key']

def get_active_instances():
    with httpx.Client(transport=httpx.HTTPTransport(uds="/var/run/docker.sock")) as client:
        # Filter for containers that have the label "miningbots-app-instance"
        r = client.get(
            "http://localhost/containers/json",
            params={"filters": '{"label":["miningbots-app-instance"]}',"all":'true'}
        )
        containers = r.json()
    return dict(map(lambda container:(os.path.basename(container['Names'][0]),{'url':f'https://{get_traefik_host(container)}','observer_key':get_observer_key(container),'running':container['State']=='running','config_dir':container['Labels'].get('configdir')}),containers))

def stop_instance(instance):
    with httpx.Client(transport=httpx.HTTPTransport(uds="/var/run/docker.sock")) as client:
        response = client.post(f"http://localhost/containers/{instance}/stop",timeout=httpx.Timeout(30.0))

        try:
            content=response.json()
        except:
            content=None
        return {'success':response.status_code==204,'rawError':content} # return true if success
def delete_instance(instance):
    with httpx.Client(transport=httpx.HTTPTransport(uds="/var/run/docker.sock")) as client:
        response = client.delete(f"http://localhost/containers/{instance}",timeout=httpx.Timeout(30.0))

        try:
            content=response.json()
        except:
            content=None
        return {'success':response.status_code==204,'rawError':content} # return true if success
def start_instance(instance):
    with httpx.Client(transport=httpx.HTTPTransport(uds="/var/run/docker.sock")) as client:
        response = client.post(f"http://localhost/containers/{instance}/start",timeout=httpx.Timeout(30.0))

        try:
            content=response.json()
        except:
            content=None
        return {'success':response.status_code==204,'rawError':content} # return true if success

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
    try:
        name=request.form.get('name')
        instance=request.form.get('instance')
        userID=containerName=f"{instance}-{name}"
        uploaddir=f"/tmp/{containerName}"
        import secrets,base64
        credentials={"userID":userID,"password":base64.b64encode(secrets.token_bytes(8)).decode()}
        with engine.connect() as conn:
            if conn.execute(text("SELECT * FROM players WHERE name=:name AND instance=:instance"),{"name":name,"instance":instance}).fetchone(): raise ConflictException
            conn.execute(text("INSERT INTO users (id, password) VALUES (:id,encode(sha256((:id||:password)::bytea),'hex'))"),{"id":credentials["userID"],"password":credentials["password"]})
            conn.execute(text("INSERT INTO players (name,instance,uploaddir,\"ownerID\") VALUES (:name,:instance,:uploaddir,:owner)"),{"name":name,"instance":instance,"uploaddir":uploaddir,"owner":credentials["userID"]})
            conn.commit()
        os.makedirs (uploaddir,exist_ok=True)
    except ConflictException:
        return render_template("new_player.html",instance=instance,error="Player name conflict. Please choose another name")
    with engine.connect() as conn:
        player_rows=conn.execute(text("SELECT name FROM players WHERE instance=:instance"),{"instance":instance}).fetchall()
        players=[player_row[0] for player_row in player_rows]
    return render_template("details.html",instance=instance,instances=get_active_instances(),players=players,showcred_player=name,showcred_creds=credentials)


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
            for row in conn.execute(text("SELECT uploaddir, \"ownerID\" FROM players WHERE instance=:instance"),{"instance":instance}).fetchall():
                uploaddir=row[0]
                shutil.rmtree(uploaddir)
                ownerIDs.append(row[1])
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

#ensure mb-instances exists
with httpx.Client(transport=httpx.HTTPTransport(uds="/var/run/docker.sock")) as client:
    response = client.post(
            "http://localhost/networks/create",
            json={
              "Name": "mb-instances",
              "Driver": "bridge",
              "CheckDuplicate": True
            }
    )
    if response.status_code!=409 and response.status_code!=201:
        raise Exception(f"failed to create network: http error {response.status_code} {response.json()}")

    # Connect the container to the mb-instances network
    response = client.post(
        "http://localhost/networks/mb-instances/connect",
        json={
            "Container": "coolify-proxy"
        }
    )

    if response.status_code!=200 and response.status_code!=409:
        print(f"failed to connect: http error {response.status_code} {response.json()}")
