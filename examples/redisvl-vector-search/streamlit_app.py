import json

import requests
import streamlit as st

st.set_page_config(page_title="Research Paper Search", layout="wide")

API_BASE = "http://localhost:8001"

st.title("Research Paper Search")
st.write("Search through 1M research papers using semantic similarity")

with st.sidebar:
    st.header("Filters")
    num_results = st.slider("Results", 1, 50, 10)

    year_filter = st.checkbox("Filter by year")
    if year_filter:
        min_year = st.number_input("From", 1900, 2024, 2000)
        max_year = st.number_input("To", 1900, 2024, 2024)

    venue_filter = st.text_input("Venue", placeholder="exact match")

    st.divider()

    try:
        resp = requests.get(f"{API_BASE}/health", timeout=3)
        if resp.status_code == 200:
            data = resp.json()
            if data["status"] == "healthy":
                st.success("System online")
                papers = data.get('papers', 0)
                if papers:
                    st.metric("Papers", f"{papers:,}")
            else:
                st.error("System offline")
        else:
            st.error("API error")
    except:
        st.error("Connection failed")


def search_papers(query):
    if not query:
        return

    filters = {}
    if year_filter:
        filters["year"] = {"min": min_year, "max": max_year}
    if venue_filter.strip():
        filters["venue"] = venue_filter.strip()

    search_data = {
        "query": query,
        "k": num_results,
        "filters": filters if filters else None
    }

    with st.spinner("Searching..."):
        try:
            response = requests.post(
                f"{API_BASE}/search",
                headers={"Content-Type": "application/json"},
                data=json.dumps(search_data),
                timeout=30)

            if response.status_code == 200:
                results = response.json()
                st.info(
                    f"Found {results['total']} results ({results['time_ms']:.0f}ms)"
                )

                if results.get("results"):
                    for i, paper in enumerate(results["results"], 1):
                        with st.container():
                            col1, col2 = st.columns([3, 1])

                            with col1:
                                st.subheader(f"{i}. {paper['title']}")
                                st.write(f"**Authors:** {paper['authors']}")
                                st.write(paper['abstract'])

                            with col2:
                                st.metric("Score", f"{paper['score']:.3f}")
                                st.write(f"Year: {paper['year']}")
                                st.write(f"Venue: {paper['venue']}")
                                st.write(f"Citations: {paper['n_citation']:,}")

                            st.markdown("---")
                else:
                    st.warning("No results found")

            else:
                st.error(f"Search failed: {response.status_code}")

        except requests.exceptions.Timeout:
            st.error("Search timeout")
        except Exception as e:
            st.error(f"Error: {str(e)}")


with st.form("search_form"):
    search_query = st.text_input(
        "Search",
        placeholder="neural networks, machine learning...",
        label_visibility="collapsed")
    search_submitted = st.form_submit_button("Search", type="primary")

if search_submitted:
    search_papers(search_query)

if not search_query:
    st.write("**Try searching for:**")

    queries = [
        "neural networks", "machine learning", "computer vision",
        "natural language processing", "deep learning", "reinforcement learning"
    ]

    cols = st.columns(3)
    for i, q in enumerate(queries):
        with cols[i % 3]:
            if st.button(q, key=f"ex_{i}"):
                search_papers(q)

st.divider()
st.caption("Built with RedisVL + SkyPilot | Data from Kaggle Research Papers")
