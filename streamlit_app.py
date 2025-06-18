import streamlit as st
import pandas as pd
import requests

# --- AUTH ---
password = st.text_input("Enter password", type="password")
if password != st.secrets["app_password"]:
    st.stop()

# --- CONFIG ---
API_KEY = st.secrets["api_key"]
HEADERS = {"accept": "application/json", "key": API_KEY}
PAGE_SIZE = 100
ESTIMATE_URL = "https://api.holded.com/api/invoicing/v1/documents/estimate"
PRODUCTS_URL = "https://api.holded.com/api/invoicing/v1/products"

# --- Fetch Estimates (LIVE) ---
def fetch_presupuestos():
    all_estimates = []
    page = 1
    while True:
        resp = requests.get(ESTIMATE_URL, headers=HEADERS, params={"page": page, "limit": PAGE_SIZE})
        resp.raise_for_status()
        chunk = resp.json().get("data", []) if isinstance(resp.json(), dict) else resp.json()
        if not chunk:
            break
        all_estimates.extend(chunk)
        if len(chunk) < PAGE_SIZE:
            break
        page += 1
    return pd.DataFrame(all_estimates)

# --- Fetch Products (LIVE) ---
def fetch_all_products():
    all_products = []
    page = 1
    while True:
        resp = requests.get(PRODUCTS_URL, headers=HEADERS, params={"page": page, "limit": PAGE_SIZE})
        resp.raise_for_status()
        data = resp.json()
        chunk = data.get("data", data) if isinstance(data, dict) else data
        if not chunk:
            break
        all_products.extend(chunk)
        if len(chunk) < PAGE_SIZE:
            break
        page += 1
    return all_products

# --- Build Lookup Table ---
def build_product_lookup(products):
    lookup = {}
    for p in products:
        pid = p.get("id") or p.get("productId")
        if not pid:
            continue
        lookup[pid] = {
            "Product": p.get("name"),
            "SKU": p.get("sku"),
            "Stock Disponible": p.get("stock")
        }
    return lookup

# --- Get Row Index by DocNumber (case-insensitive) ---
def get_row_index_by_docnumber(df, doc_number):
    lower_doc = doc_number.lower()
    matches = df.index[df['docNumber'].str.lower() == lower_doc]
    return int(matches[0]) if not matches.empty else None

# --- Build Output Table ---
def get_products_info_for_row(row_idx, df_presupuesto, product_lookup):
    row = df_presupuesto.loc[row_idx]
    items = row.get('products') or []

    records = []
    for item in items:
        pid = item.get('productId') or item.get('id')
        units = item.get('units')

        net_w = None
        ancho = alto = fondo = None
        
        for attr in item.get("attributes", []):
            raw_name = attr.get("name", "")
            name = raw_name.lower().replace('\u00a0', ' ')  # remove weird spaces
            try:
                value = float(attr.get("value"))
            except (TypeError, ValueError):
                continue
                
            if "peso neto" in name:
                net_w = value

        if net_w is None:
            net_w = item.get("weight")
                
        if not pid:
            continue
        info = product_lookup.get(pid, {})
        records.append({
            "Product": info.get("Product"),
            "SKU": info.get("SKU"),
            "Net Weight (kg)": net_w,
            "Total Weight (kg)": round(net_w * units, 3) if units is not None and net_w is not None else None,
            #"Volume": ancho,
            "Units": units,
            "Stock Disponible": info.get("Stock Disponible"),
            "Insuficiente?": "" if info.get("Stock Disponible", 0) >= units else "STOCK INSUFICIENTE"
        })
    st.write("→ Parsed:", ancho, alto, fondo)
    return pd.DataFrame(records)

# --- UI ---
st.title("📦 Presupuesto Stock Checker")
doc_input = st.text_input("Enter DocNumber:")

if doc_input:
    with st.spinner("Retrieving data..."):
        try:
            presupuesto_df = fetch_presupuestos()
            all_products = fetch_all_products()
            lookup = build_product_lookup(all_products)
            row_idx = get_row_index_by_docnumber(presupuesto_df, doc_input)

            if row_idx is None:
                st.error("DocNumber not found.")
            else:
                original_docnum = presupuesto_df.loc[row_idx, 'docNumber']
                df_result = get_products_info_for_row(row_idx, presupuesto_df, lookup)
                if df_result.empty:
                    st.warning("No product data found in the selected presupuesto.")
                else:
                    st.success(f"Presupuesto '{original_docnum}' details loaded!")
                    st.dataframe(df_result)
                    csv = df_result.to_csv(index=False).encode('utf-8')
                    st.download_button("📥 Download CSV", csv, f"{original_docnum}_stock.csv", "text/csv")
        except Exception as e:
            st.error(f"Something went wrong: {e}")

