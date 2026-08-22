"""Cliente HTTP da API, com cache — usado por todas as telas do Streamlit."""

import os

import requests
import streamlit as st

API_URL = os.getenv("API_URL", "http://localhost:8000")
API = f"{API_URL}/api/v1"


@st.cache_data(ttl=60, show_spinner=False)
def api_get(path: str, **params):
    response = requests.get(f"{API}{path}", params=params, timeout=60)
    response.raise_for_status()
    return response.json()


def clear_cache() -> None:
    api_get.clear()
