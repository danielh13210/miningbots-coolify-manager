#!/usr/bin/env python3
import requests, sys
sys.exit(0 if requests.get("http://localhost:80/healthcheck").ok else 0)
