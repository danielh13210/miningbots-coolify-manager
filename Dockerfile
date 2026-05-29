FROM python:3.13-slim-trixie
EXPOSE 80

RUN mkdir /app
WORKDIR /app

COPY requirements.txt /app
RUN pip3 install --disable-pip-version-check -r requirements.txt && rm -f requirements.txt
COPY . /app
ENV PATH="/app:$PATH"

CMD ["gunicorn","--preload","-w","4","-b","0.0.0.0:80","main:app"]
