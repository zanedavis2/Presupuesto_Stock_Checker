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
    "Proforma": "https://api.holded.com/api/invoicing/v1/documents/proform",
    "Pedido":       "https://api.holded.com/api/invoicing/v1/documents/salesorder"
}
PRODUCTS_URL = "https://api.holded.com/api/invoicing/v1/products"

# --- Fetch Documents (Estimates or Sales Orders) ---
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

# --- Fetch Products ---
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
        lookup[pid] = {
            "Product": p.get("name"),
            "SKU": p.get("sku"),
            "Stock Disponible": p.get("stock"),
            "Attributes": p.get("attributes")
        }
    return lookup

# --- Find Row by DocNumber ---
def get_row_index_by_docnumber(df, doc_number):
    lower_doc = doc_number.lower()
    matches = df.index[df['docNumber'].str.lower() == lower_doc]
    return int(matches[0]) if not matches.empty else None

# --- Build Output Table with Subtotals ---
def get_products_info_for_row(row_idx, df_docs, product_lookup):
    row = df_docs.loc[row_idx]
    items = row.get('products') or []
    if not isinstance(items, list):
        raise TypeError(f"Row {row_idx} 'products' must be a list, got {type(items)}")

    grouped = {}
    # collect per subcategory
    for item in items:
    # 1. extract whichever ID you have (even if it's None)
    pid = item.get("productId") if item.get("productId") is not None else item.get("id")
    units = item.get("units", 0)

    # 2. see if we have catalog info
    if pid is not None and pid in product_lookup:
        info = product_lookup[pid]
        attrs = info.get("Attributes") or []
        # … your existing attribute‑parsing logic here …
        sku   = info.get("SKU")
        stock = info.get("Stock Disponible", 0)
        net_w = parsed_net_weight  # from either attrs or info
    else:
        # FALLBACK: no product in catalog, take from the item itself
        info = {}
        sku   = ""
        stock = ""
        net_w = item.get("weight") or 0.0
        # if the API returns a name/description on the line:
    product_name = info.get("Product") or item.get("name") or item.get("description") or "Sin descripción"

    # 3. compute volume if the item line carried dimensions
    ancho = item.get("width_cm")
    alto  = item.get("height_cm")
    fondo = item.get("depth_cm")
    volume = None
    if None not in (ancho, alto, fondo):
        volume = round((ancho*alto*fondo)/1_000_000, 5)

    # 4. stock‑insufficiency only if we know stock
    insuf = "" if not sku or (isinstance(stock, (int,float)) and stock >= units) else "STOCK INSUFICIENTE"
    falta = 0  if insuf == "" else abs(stock-units)

    # 5. build your row dict exactly as before
    data = {
        "Product":          product_name,
        "SKU":              sku,
        "Units":            units,
        "Net Weight (kg)":  net_w,
        "Total Weight (kg)": round(net_w*units,3),
        "Volume (m³)":      volume,
        "Stock Disponible": stock,
        "Insuficiente?":    insuf,
        "Falta":            falta
    }
    # 6. decide subcategory (you can group all no‑ID under “Sin línea de productos”)
    subcat = "Sin línea de productos"
    # … or detect a “line” attribute on the item if one exists …
    grouped.setdefault(subcat, []).append(data)
    # sort products by SKU in each subcat
    for subcat in grouped:
        grouped[subcat] = sorted(grouped[subcat], key=lambda x: x.get("SKU") or "")

    output = []
    for subcat, prods in grouped.items():
        # header row
        output.append({k:"" for k in [
            "SKU","Product","Units","Subtotal > Units",
            "Net Weight (kg)","Total Weight (kg)","Subtotal > Total Weight (kg)",
            "Volume (m³)","Subtotal > Volume (m³)",
            "Stock Disponible","Insuficiente?","Falta","Subtotal > Falta"
        ]})
        output[-1]["Product"] = f"——— {subcat} ———"

        # product rows
        output.extend(prods)

        # subtotal calculations
        tmp = pd.DataFrame(prods)
        for c in ["Units","Total Weight (kg)","Volume (m³)","Falta"]:
            tmp[c] = pd.to_numeric(tmp[c],errors="coerce")
        sum_units  = tmp["Units"].sum(min_count=1)
        sum_weight = tmp["Total Weight (kg)"].sum(min_count=1)
        sum_vol    = tmp["Volume (m³)"].sum(min_count=1)
        sum_falta  = tmp["Falta"].sum(min_count=1)
        sum_units  = 0 if pd.isna(sum_units) else sum_units
        sum_weight = 0 if pd.isna(sum_weight) else sum_weight
        sum_vol    = 0 if pd.isna(sum_vol) else sum_vol
        sum_falta  = 0 if pd.isna(sum_falta) else sum_falta

        output.append({
            "SKU": "",
            "Product": f" Subtotal {subcat}",
            "Units": "",
            "Subtotal > Units": round(sum_units,1),
            "Net Weight (kg)": "",
            "Total Weight (kg)": "",
            "Subtotal > Total Weight (kg)": round(sum_weight,2),
            "Volume (m³)": "",
            "Subtotal > Volume (m³)": round(sum_vol,5),
            "Stock Disponible": "",
            "Insuficiente?": "",
            "Falta": "",
            "Subtotal > Falta": round(sum_falta,0)
        })

    if not output:
        return pd.DataFrame(columns=[
            "SKU","Product","Units","Subtotal > Units",
            "Net Weight (kg)","Total Weight (kg)","Subtotal > Total Weight (kg)",
            "Volume (m³)","Subtotal > Volume (m³)",
            "Stock Disponible","Insuficiente?","Falta","Subtotal > Falta"
        ])

    df = pd.DataFrame(output)

    # fill missing subtotals with zeros
    mask = df["Product"].str.contains("Subtotal", na=False)
    for col in ["Subtotal > Units","Subtotal > Total Weight (kg)",
                "Subtotal > Volume (m³)","Subtotal > Falta"]:
        df.loc[mask, col] = df.loc[mask, col].fillna(0)

    # enforce column order
    cols = [
        "SKU","Product","Units","Subtotal > Units",
        "Net Weight (kg)","Total Weight (kg)","Subtotal > Total Weight (kg)",
        "Volume (m³)","Subtotal > Volume (m³)",
        "Stock Disponible","Insuficiente?","Falta","Subtotal > Falta"
    ]
    return df[cols]

# --- UI ---
st.title("📦Informaciòn del Documento")

doc_type = st.selectbox("Seleccione tipo de documento", ["Presupuesto", "Proforma", "Pedido"])
url = ENDPOINTS[doc_type]
doc_input = st.text_input(f"Ingrese el número de {doc_type}:")

if doc_input:
    with st.spinner("Retrieving data..."):
        try:
            df_docs = fetch_documents(url)
            all_prods = fetch_all_products()
            lookup = build_product_lookup(all_prods)

            idx = get_row_index_by_docnumber(df_docs, doc_input)
            if idx is None:
                st.error(f"{doc_type} not found.")
            else:
                original = df_docs.loc[idx, 'docNumber']
                df_res = get_products_info_for_row(idx, df_docs, lookup)

                if df_res.empty:
                    st.warning("No valid products found.")
                else:
                    st.success(f"{doc_type} '{original}' loaded!")

                    # numeric conversion on all numeric-looking cols
                    num_cols = [
                        "Units","Subtotal > Units",
                        "Net Weight (kg)","Total Weight (kg)","Subtotal > Total Weight (kg)",
                        "Volume (m³)","Subtotal > Volume (m³)",
                        "Stock Disponible","Falta","Subtotal > Falta"
                    ]
                    for c in num_cols:
                        df_res[c] = pd.to_numeric(df_res[c], errors='coerce')

                    # overall TOTAL row
                    totals = {
                        "SKU": "",
                        "Product": "——— TOTAL ———",
                        "Units": "",
                        "Subtotal > Units": df_res["Subtotal > Units"].sum(min_count=1),
                        "Net Weight (kg)": "",
                        "Total Weight (kg)": "",
                        "Subtotal > Total Weight (kg)": df_res["Subtotal > Total Weight (kg)"].sum(min_count=1),
                        "Volume (m³)": "",
                        "Subtotal > Volume (m³)": df_res["Subtotal > Volume (m³)"].sum(min_count=1),
                        "Stock Disponible": "",
                        "Insuficiente?": "",
                        "Falta": "",
                        "Subtotal > Falta": df_res["Subtotal > Falta"].sum(min_count=1)
                    }
                    df_res = pd.concat([df_res, pd.DataFrame([totals])], ignore_index=True)

                    # styling
                    def highlight_rows(r):
                        prod = str(r["Product"])
                        if prod.startswith("———"):
                            return ["font-weight: bold; background-color: #f0f0f0"] * len(r)
                        if prod.strip().startswith("Subtotal"):
                            return ["font-weight: bold; text-align: right"] * len(r)
                        return [""] * len(r)

                    numeric_cols = [
                        "Units", "Subtotal > Units",
                        "Net Weight (kg)", "Total Weight (kg)", "Subtotal > Total Weight (kg)",
                        "Volume (m³)", "Subtotal > Volume (m³)",
                        "Stock Disponible", "Falta", "Subtotal > Falta"
                    ]
                    
                    # make sure they’re numeric (you probably already do this)
                    for c in numeric_cols:
                        df_res[c] = pd.to_numeric(df_res[c], errors="coerce")
                    
                    styled = (
                        df_res.style
                              .apply(highlight_rows, axis=1)
                              .format({
                                  "Units": "{:,.0f}",
                                  "Subtotal > Units": "{:,.0f}",
                                  "Net Weight (kg)": "{:.2f}",
                                  "Total Weight (kg)": "{:.2f}",
                                  "Subtotal > Total Weight (kg)": "{:.2f}",
                                  "Volume (m³)": "{:.3f}",
                                  "Subtotal > Volume (m³)": "{:.3f}",
                                  "Stock Disponible": "{:,.0f}",
                                  "Falta": "{:,.0f}",
                                  "Subtotal > Falta": "{:,.0f}"
                              }, na_rep="—")
                    )

                    st.dataframe(styled)

                    # pallet summary
                    total_units  = df_res["Units"].sum(min_count=1) or 0
                    total_weight = df_res["Total Weight (kg)"].sum(min_count=1) or 0
                    total_volume = df_res["Volume (m³)"].sum(min_count=1) or 0

                    pw = round(total_weight/1400,3)
                    pv = round(total_volume/2,3)
                    pallets = max(1,int(np.ceil(max(pw,pv))))

                    summary = pd.DataFrame([{
                        "Total Units": int(total_units),
                        "Total Weight (kg)": f"{total_weight:.2f} kg",
                        "Total Volume (m³)": f"{total_volume:.3f} m³",
                        "Pallets by Weight": pw,
                        "Pallets by Volume": pv,
                        "Pallets Needed": pallets
                    }])
                    st.subheader("📊 Estimated Pallet Summary")
                    st.dataframe(summary)

                    # download stock Excel
                    buf1 = io.BytesIO()
                    with pd.ExcelWriter(buf1, engine="openpyxl") as w:
                        df_res.to_excel(w, index=False)
                    buf1.seek(0)
                    st.download_button(
                        "📥 Download Excel (Stock)",
                        buf1,
                        file_name=f"{original}_stock.xlsx",
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                    )

                    # download pallets Excel
                    buf2 = io.BytesIO()
                    with pd.ExcelWriter(buf2, engine="openpyxl") as w:
                        summary.to_excel(w, index=False)
                    buf2.seek(0)
                    st.download_button(
                        "📥 Download Excel (Pallets)",
                        buf2,
                        file_name=f"{original}_pallets.xlsx",
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                    )
        except Exception as e:
            st.error(f"Something went wrong: {e}")
            full_tb = traceback.format_exc()
            traceback.print_exc()
