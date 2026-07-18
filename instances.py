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

def spawn_new_instance(username,name,config_dir,observer_key):
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
                    ],
                    "AutoRemove": True  # Automatically remove the container when it exits
            }
        }
        resp = client.post(f"http://localhost/containers/create",
                           params={"name":f"{username}-{name}"},
                           json=payload)
        if resp.status_code!=201:
            if resp.status_code==409:
                raise ConflictException
            else:
                return {'success':False,'rawError':resp.json()}
        start_url = f"http://localhost/containers/{username}-{name}/start"
        resp = client.post(start_url)
        if resp.status_code!=204: return {'success':False,'rawError':resp.json()}
        return {'success':True,'rawError':None}

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
        shared_instances=list(matches)

        result=conn.execute(text("SELECT instance, observer_key, url,config_dir FROM virtual_instances WHERE username=:me"),{"me":username})
        virtual_instances=result.fetchall()

        result=conn.execute(text("SELECT instance, observer_key, url, config_dir FROM container_instances WHERE username=:me"),{"me":username})
        containers = result.fetchall()
    def container_entry(instance, username):
        with httpx.Client(transport=httpx.HTTPTransport(uds="/var/run/docker.sock")) as client:
            r = client.get(
                f"http://localhost/containers/{username}-{instance[0]}/json"
            )
            return instance[0], {
                'url': instance[2],
                'observer_key': instance[1],
                'type': 'docker',
                'running': r.status_code==200 and r.json()['State']['Running'], # the container's are ephemeral so an error would occur if they're stopped
                'config_dir': instance[3]
            }
    def virtual_entry(instance, username):
        return instance[0], {
            'url': instance[2],
            'observer_key': instance[1],
            'type': 'virtual',
            'running': None, # we can't actually know as we don't manage it
            'config_dir': instance[3]
        }
    def shared_entry(instance_in, username):
        with db_conn.connect() as conn:
            result=conn.execute(text("SELECT username, instance, observer_key, url, config_dir FROM virtual_instances WHERE username=:source AND instance=:instance"),{"source":instance_in[0],"instance":instance_in[1]})
            instance=result.fetchone()
            if instance:
                return instance[0]+'/'+instance[1], {
                    'url': instance[3],
                    'observer_key': instance[2],
                    'type': 'virtual',
                    'running': None,
                    'config_dir': instance[4]
                }
            result=conn.execute(text("SELECT username, instance, observer_key, url, config_dir FROM container_instances WHERE username=:source AND instance=:instance"),{"source":instance_in[0],"instance":instance_in[1]})
            instance=result.fetchone()
            if instance:
                with httpx.Client(transport=httpx.HTTPTransport(uds="/var/run/docker.sock")) as client:
                    r = client.get(
                        f"http://localhost/containers/{instance[0]}-{instance[1]}/json"
                    )
                return instance[0]+'/'+instance[1], {
                    'url': instance[3],
                    'observer_key': instance[2],
                    'type': 'docker',
                    'running': r.status_code==200 and r.json()['State']['Running'], # the container's are ephemeral so an error would occur if they're stopped
                    'config_dir': instance[4]
                }
    # now build the dict
    return dict(
        [container_entry(container, username) for container in containers] +
        [virtual_entry(instance, username) for instance in virtual_instances] +
        [shared_entry(instance, username) for instance in shared_instances]
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
def delete_instance(username,instance,db_conn):
    with db_conn.connect() as conn:
        result=conn.execute(text("SELECT config_dir FROM container_instances WHERE username=:me AND instance=:instance"),{"me":username,"instance":instance})
        result=result.fetchone()
        if result:
            import shutil
            shutil.rmtree(result[0],ignore_errors=True)
            return {'success': True,'rawError': None}
        else:
            return {'success': False,'rawError': "Instance not found"}

def is_player_testserver_running(username,player,instance):
    with httpx.Client(transport=httpx.HTTPTransport(uds="/var/run/docker.sock")) as client:
        response = client.get(f"http://localhost/containers/{username}-{instance}-{player}/json")
        if response.status_code==404:
            return False
        elif response.status_code==200:
            return response.json()['State']['Running']
        else:
            raise Exception(f"failed to check test server: http error {response.status_code} {response.json()}")

def delete_player(username,player,instance):
    with httpx.Client(transport=httpx.HTTPTransport(uds="/var/run/docker.sock")) as client:
        response = client.post(
            f"http://localhost/containers/{username}-{instance}-{player}/stop",
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
