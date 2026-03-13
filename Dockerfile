FROM python:3.13-slim-trixie
EXPOSE 80

RUN mkdir /app
WORKDIR /app

RUN pip3 install flask httpx gunicorn
COPY . /app

CMD gunicorn --preload -w 4 -b 0.0.0.0:80 main:app
