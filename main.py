from flask import Flask, render_template, redirect, request, jsonify
import json
import zipfile, tarfile, stat, tempfile
import httpx
import os
import re

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
    new_base = Path("/tmp/mcm-tmp")
    original_file = Path(path)

    # Calculate the path relative to the old base, then join to the new base
    relative_path = original_file.relative_to(old_base)
    return (new_base / relative_path).as_posix()

def spawn_new_instance(name,config_dir,observer_key):
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
                "AutoRemove": True,
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
            params={"filters": '{"label":["miningbots-app-instance"]}'}
        )
        containers = r.json()
    return dict(map(lambda container:(os.path.basename(container['Names'][0]),{'url':f'https://{get_traefik_host(container)}','observer_key':get_observer_key(container)}),containers))

def stop_instance(instance):
    with httpx.Client(transport=httpx.HTTPTransport(uds="/var/run/docker.sock")) as client:
        response = client.post(f"http://localhost/containers/{instance}/stop",timeout=httpx.Timeout(30.0))

        return response.status_code==204 # return true if success

@app.route("/")
def home():
    # Render index.html from the templates folder
    return render_template("index.html", instances=get_active_instances(), frontend_url=os.environ['fe_host'])

@app.route("/new",methods=['GET'])
def new():
    # Render new.html from templates
    return render_template("new.html")
@app.route("/new",methods=['POST'])
def api_new():
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
        spawn_new_instance(name,config_dir,keys[0])
    except ConflictException:
        return render_template("new.html",error="Docker container conflict. Please choose another name")
    return redirect("/")


@app.route("/favicon.ico")
def favicon():
    return redirect("/static/favicon.ico")

@app.route("/stop",methods=['POST'])
def api_stop():
    try:
        instance=request.args['instance']
    except KeyError:
        return jsonify({"error":"instance name required"}),500
    if instance not in (containers:=get_active_instances()):
        return jsonify({"error":"instance not found"}),404
    with httpx.Client(transport=httpx.HTTPTransport(uds="/var/run/docker.sock")) as client:
        resp = client.get(f"http://localhost/containers/{instance}/json")
        if resp.status_code == 200:
            container = resp.json()
        else:
            raise Exception(f"failed to get labels: http error {resp.status_code} {resp.text}")
    configdir=container['Config']['Labels'].get('configdir')
    if stop_instance(instance):
        import shutil
        try:
            if configdir:
                shutil.rmtree(configdir)
        except:
            raise
        return "",204
    else:
        return jsonify({"error":"failed to stop"}),500

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
