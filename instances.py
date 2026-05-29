import httpx
import os
import re
import stat
import json
from sqlalchemy import text

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

def spawn_new_instance(username,name,config_dir,observer_key,start: bool=False):
    with httpx.Client(transport=httpx.HTTPTransport(uds="/var/run/docker.sock")) as client:
        payload={
            "Image": "miningbots-server",
            "Labels": {
                "miningbots-app-instance": "",
                "observer_key": str(observer_key),
                "traefik.enable": "true",
                f"traefik.http.routers.{username}-{name}-mb.rule": f'Host("{username}-{name}-mb.{os.environ['BASE_DOMAIN']}")',
                f"traefik.http.routers.{username}-{name}-mb.entrypoints": "https",
                f"traefik.http.routers.{username}-{name}-mb.tls": "true",
                f"traefik.http.routers.{username}-{name}-mb.tls.certresolver": "letsencrypt",
                f"traefik.http.services.{username}-{name}.loadbalancer.server.port": "9003",
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
        resp = client.post(f"http://localhost/containers/create",
                           params={"name":f"{username}-{name}"},
                           json=payload)
        if resp.status_code!=201:
            if resp.status_code==409:
                raise ConflictException
            else:
                raise Exception(f"cannot create: http error {resp.status_code} {resp.json()}")
        if start:
            start_url = f"http://localhost/containers/{username}-{name}/start"
            resp = client.post(start_url)
            if resp.status_code!=204: raise Exception(f"cannot start: http error {resp.status_code} {resp.json()}")

def spawn_player(username, player, instance, instances):
    with httpx.Client(transport=httpx.HTTPTransport(uds="/var/run/docker.sock")) as client:
        payload={
            "Image": "miningbots-server",
            "Labels": {
                "traefik.enable": "true",
                f"traefik.http.routers.{username}-{instance}-{player}-mb.rule": f'Host("{username}-{instance}-{player}-mb.{os.environ['BASE_DOMAIN']}")',
                f"traefik.http.routers.{username}-{instance}-{player}-mb.entrypoints": "https",
                f"traefik.http.routers.{username}-{instance}-{player}-mb.tls": "true",
                f"traefik.http.routers.{username}-{instance}-{player}-mb.tls.certresolver": "letsencrypt",
                f"traefik.http.services.{username}-{instance}-{player}.loadbalancer.server.port": "9003",
                "observer_key": instances[instance]['observer_key']
            },
            "HostConfig": {
                "NetworkMode": "mb-instances",
                    "Mounts": [
                        {
                            "Type": "bind",
                            "Source": rebase_path_for_docker(instances[instance]['config_dir']),
                            "Target": "/miningbots-server/config",
                            "ReadOnly": True
                        }
                    ]
            }
        }
        resp = client.post(f"http://localhost/containers/create",
                           params={"name":f"{username}-{instance}-{player}"},
                           json=payload)
        if resp.status_code!=201:
            if resp.status_code==409:
                raise ConflictException
            else:
                raise Exception(f"cannot create: http error {resp.status_code} {resp.json()}")

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

def parse_container_name(username,container_name):
    if '/' in container_name:
        return container_name.split('/',maxsplit=1)
    else:
        return username, container_name
    
def get_active_instances(username,db_conn):
    with db_conn.connect() as conn:
        result=conn.execute(text("SELECT share_source, instance FROM shared_instances WHERE share_destination=:me"),{"me":username})
        matches=result.fetchall()
        additional_container_names=list(map(lambda row:row[0]+'-'+row[1],matches))
    with httpx.Client(transport=httpx.HTTPTransport(uds="/var/run/docker.sock")) as client:
        # Filter for containers that have the label "miningbots-app-instance"
        r = client.get(
            "http://localhost/containers/json",
            params={"filters": json.dumps({"label":["miningbots-app-instance"],"name":[f"^{username}-.*"]+additional_container_names}),"all":'true'}
        )
        containers = r.json()
    def container_entry(container, username):
        # derive the name
        raw_name = os.path.basename(container['Names'][0])
        if raw_name.startswith(username):
            name = raw_name.split('-', maxsplit=1)[1]
        else:
            name = '/'.join(raw_name.split('-', maxsplit=1))

        # build the value dict
        return name, {
            'url': f'https://{get_traefik_host(container)}',
            'observer_key': get_observer_key(container),
            'running': container['State'] == 'running',
            'config_dir': container['Labels'].get('configdir'),
        }

    # now build the dict
    return dict(
        container_entry(container, username) for container in containers
    )

def stop_instance(username,instance):
    with httpx.Client(transport=httpx.HTTPTransport(uds="/var/run/docker.sock")) as client:
        user, name = parse_container_name(username, instance)
        response = client.post(f"http://localhost/containers/{user}-{name}/stop",timeout=httpx.Timeout(30.0))

        try:
            content=response.json()
        except:
            content=None
        return {'success':response.status_code==204,'rawError':content} # return true if success
def delete_instance(username,instance):
    with httpx.Client(transport=httpx.HTTPTransport(uds="/var/run/docker.sock")) as client:
        user, name = parse_container_name(username, instance)
        response = client.delete(f"http://localhost/containers/{user}-{name}",timeout=httpx.Timeout(30.0))

        try:
            content=response.json()
        except:
            content=None
        return {'success':response.status_code==204,'rawError':content} # return true if success
def start_instance(username,instance):
    with httpx.Client(transport=httpx.HTTPTransport(uds="/var/run/docker.sock")) as client:
        user, name = parse_container_name(username, instance)
        response = client.post(f"http://localhost/containers/{user}-{name}/start",timeout=httpx.Timeout(30.0))

        try:
            content=response.json()
        except:
            content=None
        return {'success':response.status_code==204,'rawError':content} # return true if success

def delete_player(username,player,instance):
    with httpx.Client(transport=httpx.HTTPTransport(uds="/var/run/docker.sock")) as client:
        candidate = f"{instance}-{player}"
        user, rest = parse_container_name(username, candidate)
        response = client.delete(
            f"http://localhost/containers/{user}-{rest}",
            params={'force':'true'},
            timeout=httpx.Timeout(30.0)
        )

        try:
            content=response.json()
        except:
            content=None
        return {'success':response.status_code==204,'rawError':content} # return true if success

def setup_networking():
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
