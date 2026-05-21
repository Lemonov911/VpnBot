#!/usr/bin/env python3
"""Simple benchmark server - serves test file for protocol testing"""
import json
import time
from datetime import datetime
from flask import Flask, jsonify
import os

app = Flask(__name__)

# Store test results
TEST_RESULTS = {"timestamp": None, "tests": []}


@app.route("/health")
def health():
    """Health check"""
    return jsonify({
        "status": "ok",
        "timestamp": datetime.utcnow().isoformat(),
        "protocols": ["vless_reality", "shadowsocks", "http_tunnel", "openvpn"]
    })


@app.route("/test/image")
def test_image():
    """Return 10MB test file"""
    # Generate 10MB of random data
    data = os.urandom(10 * 1024 * 1024)
    return data, 200, {"Content-Type": "application/octet-stream"}


@app.route("/results")
def get_results():
    """Get stored test results"""
    return jsonify(TEST_RESULTS)


@app.route("/ping")
def ping():
    """Simple latency test"""
    return jsonify({"pong": True, "time": time.time()})


if __name__ == "__main__":
    # Run on all interfaces, port 5000
    app.run(host="0.0.0.0", port=5000, debug=False)
