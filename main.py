from flask import Flask, render_template, redirect

app = Flask(__name__)

@app.route("/")
def home():
    # Render index.html from the templates folder
    return render_template("index.html", instances=['test'])

@app.route("/favicon.ico")
def favicon():
    return redirect("/static/favicon.ico")

if __name__ == "__main__":
    app.run(debug=False,port=80,host='0.0.0.0')
