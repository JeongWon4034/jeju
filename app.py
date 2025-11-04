import streamlit as st
import geopandas as gpd
import pandas as pd
import folium
from folium.plugins import MarkerCluster
from shapely.geometry import Point
import osmnx as ox
import requests
from streamlit_folium import st_folium
import openai
import math
import os

# ──────────────────────────────
# ✅ 환경변수 불러오기 (Streamlit Cloud 호환에 저장된 키 사용)
# ──────────────────────────────
MAPBOX_TOKEN = st.secrets["MAPBOX_TOKEN"]
openai.api_key = st.secrets["OPENAI_API_KEY"]

# ──────────────────────────────
# ✅ 데이터 로드
# ──────────────────────────────
gdf = gpd.read_file("cb_tour.shp").to_crs(epsg=4326)
gdf["lon"], gdf["lat"] = gdf.geometry.x, gdf.geometry.y
boundary = gpd.read_file("cb_shp.shp").to_crs(epsg=4326)
data = pd.read_csv("cj_data_final.csv", encoding="cp949").drop_duplicates()

# ──────────────────────────────
# ✅ Session 초기화
# ──────────────────────────────
DEFAULTS = {
    "order": [],
    "segments": [],
    "duration": 0.0,
    "distance": 0.0,
    "messages": [{"role": "system", "content": "당신은 청주 문화관광 전문 가이드입니다."}],
    "auto_gpt_input": ""
}
for k, v in DEFAULTS.items():
    if k not in st.session_state:
        st.session_state[k] = v

# ──────────────────────────────
# ✅ CSS & 스타일
# ──────────────────────────────
st.set_page_config(page_title="청주시 경유지 & GPT 가이드", layout="wide")
st.markdown("""
<style>
  html, body, [class*="css"] {
    font-family: 'Inter', sans-serif;
    background: #f9fafb;
    color: #333;
  }
  .card {
    background: #fff;
    border-radius: 12px;
    padding: 20px;
    box-shadow: 0 4px 12px rgba(0,0,0,0.06);
    margin-bottom: 20px;
  }
  .stButton>button {
    border-radius: 8px;
    font-weight: 600;
    padding: 12px 24px;
    width: 100%;
  }
  .btn-create { background: linear-gradient(90deg,#00C9A7,#008EAB); color: #fff; }
  .btn-clear { background: #E63946; color: #fff; }
</style>
""", unsafe_allow_html=True)

# ──────────────────────────────
# ✅ 상단 타이틀
# ──────────────────────────────
st.markdown("<h1 style='text-align:center;'>📍 청주시 경로 & GPT 대시보드</h1>", unsafe_allow_html=True)

# ──────────────────────────────
# ✅ 컬럼: 좌 → 우 UX 흐름
# ──────────────────────────────
col1, col2, col3, col4 = st.columns([1.5, 1, 1, 3], gap="large")

# ------------------------------
# ✅ [좌] 경로 설정
# ------------------------------
with col1:
    st.markdown("<div class='card'>", unsafe_allow_html=True)
    st.subheader("🚗 경로 설정")
    mode = st.radio("이동 모드", ["driving", "walking"], horizontal=True)
    start = st.selectbox("출발지", gdf["name"].dropna().unique())
    wps = st.multiselect("경유지", [n for n in gdf["name"].dropna().unique() if n != start])
    create_clicked = st.button("✅ 경로 생성")
    clear_clicked = st.button("🚫 초기화")
    st.markdown("</div>", unsafe_allow_html=True)

# ------------------------------
# ✅ [중간] 방문 순서
# ------------------------------
with col2:
    st.markdown("<div class='card'>", unsafe_allow_html=True)
    st.subheader("🔢 방문 순서")
    if st.session_state["order"]:
        for i, name in enumerate(st.session_state["order"], 1):
            st.markdown(f"{i}. {name}")
    else:
        st.markdown("<span style='color:#aaa'>경로 생성 후 표시됩니다.</span>", unsafe_allow_html=True)
    st.markdown("</div>", unsafe_allow_html=True)

# ------------------------------
# ✅ [중간] KPI 카드
# ------------------------------
with col3:
    st.markdown("<div class='card'>예상 소요 시간</div>", unsafe_allow_html=True)
    st.subheader(f"{st.session_state['duration']:.1f} 분")
    st.markdown("<div class='card'>예상 이동 거리</div>", unsafe_allow_html=True)
    st.subheader(f"{st.session_state['distance']:.2f} km")

# ------------------------------
# ✅ [우] 지도 + GPT
# ------------------------------
with col4:
    ctr = boundary.geometry.centroid
    clat, clon = float(ctr.y.mean()), float(ctr.x.mean())
    if math.isnan(clat): clat, clon = 36.64, 127.48

    @st.cache_data
    def load_graph(lat, lon):
        return ox.graph_from_point((lat, lon), dist=3000, network_type="all")
    G = load_graph(clat, clon)

    stops = [start] + wps
    snapped = []
    for nm in stops:
        row = gdf[gdf["name"] == nm].iloc[0]
        lon, lat = row.lon, row.lat
        node_id = ox.nearest_nodes(G, lon, lat)
        node_data = G.nodes[node_id]
        snapped.append((node_data['x'], node_data['y']))

    if clear_clicked:
        for k in ["segments", "order", "duration", "distance"]:
            st.session_state.pop(k, None)

    if create_clicked and len(snapped) >= 2:
        coords = ";".join([f"{x},{y}" for x, y in snapped])
        url = f"https://api.mapbox.com/optimized-trips/v1/mapbox/{mode}/{coords}" if len(snapped) > 2 \
            else f"https://api.mapbox.com/directions/v5/mapbox/{mode}/{coords}"
        key = "trips" if len(snapped) > 2 else "routes"
        params = {
            "geometries": "geojson", "overview": "full",
            "source": "first", "destination": "last", "roundtrip": "false",
            "access_token": MAPBOX_TOKEN
        } if len(snapped) > 2 else {
            "geometries": "geojson", "overview": "full", "access_token": MAPBOX_TOKEN
        }
        r = requests.get(url, params=params)
        data_resp = r.json() if r.status_code == 200 else {}
        if key in data_resp and data_resp[key]:
            route = data_resp[key][0]
            st.session_state["segments"] = [route["geometry"]["coordinates"]]
            st.session_state["duration"] = route["duration"] / 60
            st.session_state["distance"] = route["distance"] / 1000
            st.session_state["order"] = stops
        else:
            st.warning("❌ 경로 생성 실패!")

    m = folium.Map(location=[clat, clon], zoom_start=12)
    folium.GeoJson(boundary).add_to(m)
    mc = MarkerCluster().add_to(m)
    for _, row in gdf.iterrows():
        folium.Marker([row.lat, row.lon], popup=row.name).add_to(mc)
    for idx, ((x, y), name) in enumerate(zip(snapped, st.session_state.get("order", stops)), 1):
        folium.Marker([y, x], tooltip=f"{idx}. {name}",
                      icon=folium.Icon(color="#008EAB", icon="flag")).add_to(m)
    if st.session_state["segments"]:
        for seg in st.session_state["segments"]:
            folium.PolyLine([(pt[1], pt[0]) for pt in seg], color="red").add_to(m)
    st_folium(m, width="100%", height=400)

    st.markdown("---")
    st.subheader("🏛️ GPT 가이드")
    if st.button("🔁 방문 순서 자동 입력"):
        st.session_state["auto_gpt_input"] = ", ".join(st.session_state.get("order", []))
    with st.form("chat_form"):
        user_input = st.text_input("관광지명 쉼표로", value=st.session_state.get("auto_gpt_input", ""))
        submitted = st.form_submit_button("보내기")
    if submitted and user_input:
        st.session_state["messages"].append({"role": "user", "content": user_input})
        gpt_reply = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": "너는 청주 관광 가이드야."},
                {"role": "user", "content": user_input}
            ]
        ).choices[0].message.content
        st.session_state["messages"].append({"role": "assistant", "content": gpt_reply})
    for msg in st.session_state["messages"][1:]:
        align = "right" if msg["role"] == "user" else "left"
        bg = "#dcf8c6" if msg["role"] == "user" else "#fff"
        st.markdown(f"<div style='text-align:{align};background:{bg};padding:8px;border-radius:10px;margin-bottom:6px'>{msg['content']}</div>", unsafe_allow_html=True)
