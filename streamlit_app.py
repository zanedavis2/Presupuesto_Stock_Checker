import streamlit as st
import pandas as pd
import requests
import numpy as np

# --- AUTH ---
password = st.text_input("🔐Ingrese la contraseña", type="password")
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
            "Stock Disponible": p.get("stock"),
            "Attributes": p.get("attributes")
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

        # Skip if no product ID
        if not pid:
            continue

        # Get product info from lookup
        info = product_lookup.get(pid, {})

        # Initialize fields
        net_w = None
        ancho = alto = fondo = None
        volume = None

        # Extract attributes
        for attr in info.get("Attributes") or []:
            name = attr.get("name", "")
            
            # Try to convert value to float
            try:
                value = float(attr.get("value"))
            except (TypeError, ValueError):
                continue

            if name == "Peso Neto":
                net_w = value
            elif name == "Ancho [cm]":
                ancho = value
            elif name == "Alto [cm]":
                alto = value
            elif name == "Fondo [cm]":
                fondo = value

        # Fallback to 'weight' field if needed
        if net_w is None:
            net_w = item.get("weight")

        # Calculate volume if all dimensions are available
        if None not in (ancho, alto, fondo):
            volume = round((ancho * alto * fondo) / 1_000_000, 5)


        # Append row
        records.append({
            "Product": info.get("Product"),
            "SKU": info.get("SKU"),
            "Net Weight (kg)": net_w,
            "Total Weight (kg)": round(net_w * units, 3) if units is not None and net_w is not None else None,
            "Volume (m³)": volume,
            "Units": units,
            "Stock Disponible": info.get("Stock Disponible"),
            "Insuficiente?": "" if not info.get("SKU") or info.get("Stock Disponible", 0) >= units else "STOCK INSUFICIENTE",
            "Falta": "" if info.get("Stock Disponible", 0) >= units else f"{abs(info.get('Stock Disponible', 0) - units)}"
        })

    return pd.DataFrame(records)

# --- UI ---
st.title("📦 Presupuesto Stock")
doc_input = st.text_input("Ingrese el DocNumber:")

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

                    if not df_result.empty:
                        total_units = df_result["Units"].sum()
                        total_weight = df_result["Total Weight (kg)"].sum(min_count=1)
                        total_volume = df_result["Volume (m³)"].sum(min_count=1)
                    
                        # Handle None values
                        total_weight = total_weight if pd.notnull(total_weight) else 0.0
                        total_volume = total_volume if pd.notnull(total_volume) else 0.0
                    
                        pallets_by_weight = round(total_weight / 1400, 3)
                        pallets_by_volume = round(total_volume / 2, 3)
                        estimated_pallets = int(np.ceil(max(pallets_by_weight, pallets_by_volume)))
                        if estimated_pallets == 0:
                                estimated_pallets = 1
                    
                        # Summary table as a DataFrame
                        summary_df = pd.DataFrame({
                            "numProducts": [total_units, "", ""],
                            "WEIGHT": [f"{total_weight:.2f} kg", "", ""],
                            "VOLUME": [f"{total_volume:.3f} m³", "", ""],
                            "PALLETS": [pallets_by_weight, pallets_by_volume, estimated_pallets],
                            "": [
                                "Estimated Pallets by Weight",
                                "Estimated Pallets by Volume",
                                "PALLETS NEEDED"
                            ]
                        })
                        
                        # Set custom row labels: only first row has "TOTAL"
                        summary_df.index = ["TOTAL", "", ""]
                        
                        # Reorder columns so Description is at the far right
                        summary_df = summary_df[["numProducts", "WEIGHT", "VOLUME", "PALLETS", ""]]
                    
                        st.subheader("📊 Estimated Pallet Summary")
                        st.dataframe(summary_df)

                    
                    csv = df_result.to_csv(index=False).encode('utf-8')
                    st.download_button("📥 Download Product Table as CSV", csv, f"{original_docnum}_stock.csv", "text/csv")

                    csv = summary_df.to_csv(index=False).encode('utf-8')
                    st.download_button("📥 Download Pallet Table as CSV", csv, f"{original_docnum}_pallets.csv", "text/csv")
        
        except Exception as e:
            st.error(f"Something went wrong: {e}")

