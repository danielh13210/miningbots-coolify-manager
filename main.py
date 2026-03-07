from flask import Flask, render_template, redirect, request, jsonify
import httpx
import os
import re

app = Flask(__name__)

traefik_rule_matcher=re.compile(r'traefik\..*\.rule')
get_host=re.compile(r'Host\("(.*)"\)')

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

if __name__ == "__main__":
    app.run(debug=False,port=80,host='0.0.0.0')
