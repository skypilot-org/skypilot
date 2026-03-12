"""Simple Streamlit app example for SkyPilot deployment."""
import numpy as np
import pandas as pd
import streamlit as st

st.set_page_config(page_title="SkyPilot Streamlit Demo", layout="wide")

st.title("SkyPilot + Streamlit Demo")
st.write(
    "A simple example showing how to deploy your Streamlit app with SkyPilot")

with st.sidebar:
    st.header("Settings")
    num_points = st.slider("Number of data points", 10, 1000, 100)
    chart_type = st.selectbox("Chart type", ["Line", "Bar", "Area"])

st.subheader("Random Data Visualization")
data = pd.DataFrame({
    'x': range(num_points),
    'y': np.random.randn(num_points).cumsum()
})

if chart_type == "Line":
    st.line_chart(data.set_index('x'))
elif chart_type == "Bar":
    st.bar_chart(data.set_index('x'))
else:
    st.area_chart(data.set_index('x'))

if st.checkbox("Show raw data"):
    st.dataframe(data)

st.divider()
st.caption("Deployed with SkyPilot")
