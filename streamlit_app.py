import streamlit as st
import pandas as pd
import requests
import numpy as np
import io
import traceback

# --- AUTH ---
password = st.text_input("🔐Ingrese la contraseña", type="password")
if password != st.secrets["app_password"]:
    st.stop()

# --- CONFIG ---
API_KEY = st.secrets["api_key"]
HEADERS = {"accept": "application/json", "key": API_KEY}
PAGE_SIZE = 100
ENDPOINTS = {
    "Presupuesto": "https://api.holded.com/api/invoicing/v1/documents/estimate",
    "Pedido":       "https://api.holded.com/api/invoicing/v1/documents/salesorder"
}
PRODUCTS_URL = "https://api.holded.com/api/invoicing/v1/products"

# --- Fetch Documents (either estimates or sales orders) ---
def fetch_documents(url):
    all_docs = []
    page = 1
    while True:
        resp = requests.get(url, headers=HEADERS, params={"page": page, "limit": PAGE_SIZE})
        resp.raise_for_status()
        data = resp.json()
        chunk = data.get("data", data) if isinstance(data, dict) else data
        if not chunk:
            break
        all_docs.extend(chunk)
        if len(chunk) < PAGE_SIZE:
            break
        page += 1
    return pd.DataFrame(all_docs)

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

# --- Build Output Table (unchanged) ---
def get_products_info_for_row(row_idx, df_presupuesto, product_lookup):
    row = df_presupuesto.loc[row_idx]
    items = row.get('products') or []

    if not isinstance(items, list):
        raise TypeError(f"Row {row_idx} 'products' must be a list, got {type(items)}")

    grouped = {}
    for item in items:
        pid = item.get('productId') or item.get('id')
        units = item.get('units')
        if not pid or pid not in product_lookup:
            continue
        info = product_lookup.get(pid, {})
        attributes = info.get("Attributes") or []

        net_w = None
        ancho = alto = fondo = None
        volume = None
        subcategory = "Sin línea de productos"

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

    output = []
    for subcat, products in grouped.items():
        # Header
        output.append({k: "" for k in [
            "Product","SKU","Net Weight (kg)","Total Weight (kg)",
            "Volume (m³)","Units","Stock Disponible","Insuficiente?","Falta"
        ]})
        output[-1]["Product"] = f"——— {subcat} ———"
        # Items
        output.extend(products)
        # Subtotal
        subtotal_df = pd.DataFrame(products)
        for col in ["Total Weight (kg)", "Volume (m³)", "Units", "Falta"]:
            subtotal_df[col] = pd.to_numeric(subtotal_df[col], errors="coerce")
        num_falta = subtotal_df['Falta'].sum(min_count=1)
        num_falta = 0 if pd.isna(num_falta) else num_falta
        output.append({
            "Product": " Subtotal",
            "SKU": "",
            "Net Weight (kg)": "",
            "Total Weight (kg)": round(subtotal_df["Total Weight (kg)"].sum(min_count=1), 2),
            "Volume (m³)": round(subtotal_df["Volume (m³)"].sum(min_count=1), 5),
            "Units": round(subtotal_df["Units"].sum(min_count=1), 1),
            "Stock Disponible": "",
            "Insuficiente?": f"Falta: {num_falta:.0f}",
            "Falta": ""
        })

    if not output:
        return pd.DataFrame(columns=[
            "Product","SKU","Net Weight (kg)","Total Weight (kg)",
            "Volume (m³)","Units","Stock Disponible","Insuficiente?","Falta"
        ])

    df = pd.DataFrame(output)
    expected_cols = [
        "Product","SKU","Net Weight (kg)","Total Weight (kg)",
        "Volume (m³)","Units","Stock Disponible","Insuficiente?","Falta"
    ]
    for col in expected_cols:
        if col not in df.columns:
            df[col] = None
    return df[expected_cols]

# --- UI ---
st.title("📦 Presupuesto / Pedido Stock")

# choose document type
doc_type = st.selectbox("Seleccione tipo de documento", ["Presupuesto", "Pedido"])
url = ENDPOINTS[doc_type]

# input number
doc_input = st.text_input(f"Ingrese el número de {doc_type}:")

if doc_input:
    with st.spinner("Retrieving data..."):
        try:
            # fetch the chosen docs + products
            df_docs = fetch_documents(url)
            all_products = fetch_all_products()
            lookup = build_product_lookup(all_products)

            row_idx = get_row_index_by_docnumber(df_docs, doc_input)
            if row_idx is None:
                st.error(f"{doc_type} not found.")
            else:
                original_docnum = df_docs.loc[row_idx, 'docNumber']
                df_result = get_products_info_for_row(row_idx, df_docs, lookup)

                if df_result.empty:
                    st.warning("No valid products found. They may lack SKUs or attributes.")
                else:
                    st.success(f"{doc_type} '{original_docnum}' details loaded!")

                    # styling
                    def highlight_headers(row):
                        prod = str(row["Product"])
                        if prod.startswith("———"):
                            return ["font-weight: bold; background-color: #f0f0f0"] * len(row)
                        return [""] * len(row)

                    styled_df = (
                        df_result.style
                                 .apply(highlight_headers, axis=1)
                                 .format({
                                    "Net Weight (kg)": "{:.2f}",
                                    "Total Weight (kg)": "{:.2f}",
                                    "Volume (m³)": "{:.3f}",
                                    "Units": "{:,.0f}",
                                    "Stock Disponible": "{:,.0f}",
                                    "Falta": "{:,.0f}",
                                }, na_rep="—")
                    )

                    st.dataframe(styled_df)

                    # pallet summary
                    total_units  = df_result["Units"].sum()
                    total_weight = df_result["Total Weight (kg)"].sum(min_count=1) or 0
                    total_volume = df_result["Volume (m³)"].sum(min_count=1) or 0

                    pw = round(total_weight / 1400, 3)
                    pv = round(total_volume / 2, 3)
                    pallets = max(1, int(np.ceil(max(pw, pv))))

                    summary_df = pd.DataFrame([{
                        "Total Units": int(total_units),
                        "Total Weight (kg)": f"{total_weight:.2f} kg",
                        "Total Volume (m³)": f"{total_volume:.3f} m³",
                        "Pallets by Weight": pw,
                        "Pallets by Volume": pv,
                        "Pallets Needed": pallets
                    }])

                    st.subheader("📊 Estimated Pallet Summary")
                    st.dataframe(summary_df)

                    # download Excel (stock)
                    buf1 = io.BytesIO()
                    with pd.ExcelWriter(buf1, engine="openpyxl") as w:
                        df_result.to_excel(w, index=False, sheet_name="Sheet1")
                    buf1.seek(0)
                    st.download_button(
                        "📥 Download Excel (Stock)",
                        data=buf1,
                        file_name=f"{original_docnum}_stock.xlsx",
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                    )

                    # download Excel (pallets)
                    buf2 = io.BytesIO()
                    with pd.ExcelWriter(buf2, engine="openpyxl") as w:
                        summary_df.to_excel(w, index=False, sheet_name="Sheet1")
                    buf2.seek(0)
                    st.download_button(
                        "📥 Download Excel (Pallets)",
                        data=buf2,
                        file_name=f"{original_docnum}_pallets.xlsx",
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                    )

        except Exception as e:
            st.error(f"Something went wrong: {e}")
            tb = traceback.format_exc()
        
            # 3) Display it (you can also use st.code or put it in an expander)
            with st.expander("Show full error details"):
                st.text(tb)
