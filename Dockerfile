FROM python:3.13-slim-trixie
EXPOSE 80

RUN mkdir /app
WORKDIR /app

RUN pip3 install flask
COPY . /app

CMD python3 ./main.py
