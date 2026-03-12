from flask import Flask, render_template, redirect, request, jsonify
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

def spawn_new_instance(name,config_dir):
    with httpx.Client(transport=httpx.HTTPTransport(uds="/var/run/docker.sock")) as client:
        payload={
            "Image": "miningbots-server",
            "Labels": {
                "miningbots-app-instance": "",
                "traefik.enable": "true",
                f"traefik.http.routers.{name}-mb.rule": f'Host("{name}-mb.fried.tinkertofu.com")',
                f"traefik.http.routers.{name}-mb.entrypoints": "https",
                f"traefik.http.routers.{name}-mb.tls": "true",
                f"traefik.http.routers.{name}-mb.tls.certresolver": "letsencrypt",
                f"traefik.http.services.{name}.loadbalancer.server.port": "9003"
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

def get_active_instances():
    with httpx.Client(transport=httpx.HTTPTransport(uds="/var/run/docker.sock")) as client:
        # Filter for containers that have the label "miningbots-app-instance"
        r = client.get(
            "http://localhost/containers/json",
            params={"filters": '{"label":["miningbots-app-instance"]}'}
        )
        containers = r.json()
    return dict(map(lambda container:(os.path.basename(container['Names'][0]),get_traefik_host(container)),containers))

def stop_instance(instance):
    with httpx.Client(transport=httpx.HTTPTransport(uds="/var/run/docker.sock")) as client:
        response = client.post(f"http://localhost/containers/{instance}/stop",timeout=httpx.Timeout(30.0))

        return response.status_code==204 # return true if success

@app.route("/")
def home():
    # Render index.html from the templates folder
    return render_template("index.html", instances=get_active_instances())

@app.route("/new",methods=['GET'])
def new():
    # Render new.html from templates
    return render_template("new.html")
@app.route("/new",methods=['POST'])
def api_new():
    try:
        config_zip=request.files.get('config-zip')
        config_dir=tempfile.mkdtemp()
        os.close((temp:=tempfile.mkstemp())[0]);file=temp[1];del temp
        config_zip.save(file)
        import shutil
        for mixin_file in os.listdir('mixin-config'):
            shutil.copy(os.path.join('mixin-config',mixin_file),config_dir)
        with zipfile.ZipFile(file, 'r') as zip_device:
            safe_extract(zip_device, config_dir)
        spawn_new_instance(request.form.get('name'),config_dir)
    except ConflictException:
        return render_template("new.html",error="Docker container conflict. Please choose another name")
    return redirect("/")


@app.route("/favicon.ico")
def favicon():
    return redirect("/static/favicon.ico")

@app.route("/stop")
def stop():
    try:
        instance=request.args['instance']
    except KeyError:
        return jsonify({"error":"instance name required"}),500
    if stop_instance(instance):
        return "",204
    else:
        return jsonify({"error":"failed to stop"}),500
