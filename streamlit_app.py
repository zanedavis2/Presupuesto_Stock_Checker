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

    if not isinstance(items, list):
        raise TypeError(f"Row {row_idx} 'products' must be a list, got {type(items)}")

    grouped = {}

    for item in items:
        pid = item.get('productId') or item.get('id')
        units = item.get('units')

        if not pid:
            continue

        info = product_lookup.get(pid, {})
        attributes = info.get("Attributes") or []

        # Initialize values
        net_w = None
        ancho = alto = fondo = None
        volume = None
        subcategory = "Sin Product Line"

        for attr in attributes:
            name = attr.get("name", "")
            raw_value = attr.get("value")

            if name == "Product Line":
                subcategory = raw_value or subcategory
                continue

            try:
                value = float(raw_value)
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

        if net_w is None:
            net_w = item.get("weight") or info.get("Net Weight")

        if None not in (ancho, alto, fondo):
            volume = round((ancho * alto * fondo) / 1_000_000, 5)

        stock = info.get("Stock Disponible", 0)
        insuf = "" if not info.get("SKU") or stock >= units else "STOCK INSUFICIENTE"
        falta = "" if stock >= units else abs(stock - units)

        product_data = {
            "Product": info.get("Product"),
            "SKU": info.get("SKU"),
            "Net Weight (kg)": net_w,
            "Total Weight (kg)": round(net_w * units, 3) if units is not None and net_w is not None else None,
            "Volume (m³)": volume,
            "Units": units,
            "Stock Disponible": stock,
            "Insuficiente?": insuf,
            "Falta": falta,
        }

        grouped.setdefault(subcategory, []).append(product_data)

    # Create output with subcategory headers and totals
    output = []
    for subcat, products in grouped.items():
        output.append({col: None for col in [
            "Product", "SKU", "Net Weight (kg)", "Total Weight (kg)",
            "Volume (m³)", "Units", "Stock Disponible", "Insuficiente?", "Falta"
        ]})
        output[-1]["Product"] = f"——— {subcat} ———"

        output.extend(products)

        # Add subcategory total row
        subtotal_df = pd.DataFrame(products)
        for col in ["Total Weight (kg)", "Volume (m³)", "Units", "Falta"]:
            subtotal_df[col] = pd.to_numeric(subtotal_df[col], errors="coerce")
                                             
        total_row = {
            "Product": "           " + "Subtotal",
            "SKU": None,
            "Net Weight (kg)": None,
            "Total Weight (kg)": subtotal_df["Total Weight (kg)"].sum(min_count=1),
            "Volume (m³)": subtotal_df["Volume (m³)"].sum(min_count=1),
            "Units": subtotal_df["Units"].sum(min_count=1),
            "Stock Disponible": subtotal_df["Stock Disponible"].sum(min_count=1),
            "Insuficiente?": None,
            "Falta": subtotal_df["Falta"].sum(min_count=1)
        }
        output.append(total_row)

    # Create final DataFrame
    df = pd.DataFrame(output)

    # Ensure consistent column order
    cols = [
        "Product", "SKU", "Net Weight (kg)", "Total Weight (kg)",
        "Volume (m³)", "Units", "Stock Disponible", "Insuficiente?", "Falta"
    ]
    df = df[cols]

    return df
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
                    # Convert numeric columns safely
                    for col in ["Net Weight (kg)", "Total Weight (kg)", "Volume (m³)", "Units", "Stock Disponible", "Falta"]:
                        df_result[col] = pd.to_numeric(df_result[col], errors='coerce')
                    
                    # Style function for subcategory header rows
                    def highlight_subcategories(row):
                        if row['Product'] and all(pd.isna(row[col]) for col in row.index if col != 'Product'):
                            return ['font-weight: bold; background-color: #f0f0f0'] * len(row)
                        if row['Product'] == "——— Subtotal":
                            return ['font-weight: bold; text-align: right'] * len(row)
                            
                        return [''] * len(row)
                    
                    # Apply styling and formatting
                    styled_df = (
                        df_result
                        .style
                        .apply(highlight_subcategories, axis=1)
                        .format({
                            "Net Weight (kg)": "{:.1f}",
                            "Total Weight (kg)": "{:.2f}",
                            "Volume (m³)": "{:.3f}",
                            "Units": "{:,.0f}",
                            "Stock Disponible": "{:,.0f}",
                            "Falta": "{:,.0f}",
                        }, na_rep="—")  # 👈 this replaces NaN/None with blank
                    )

                    st.dataframe(styled_df)

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

