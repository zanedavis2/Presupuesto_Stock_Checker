import streamlit as st
import pandas as pd
import requests
import numpy as np
import io

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
            "Stock Real": p.get("stock"),
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

        if not pid or pid not in product_lookup:
            continue

        info = product_lookup.get(pid, {})
        attributes = info.get("Attributes") or []

        # Initialize values
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

        stock = info.get("Stock Real", 0)
        insuf = "" if not info.get("SKU") or stock >= units else "STOCK INSUFICIENTE"
        falta = "" if stock >= units else abs(stock - units)

        product_data = {
            "Product": info.get("Product"),
            "SKU": info.get("SKU"),
            "Net Weight (kg)": net_w,
            "Total Weight (kg)": round(net_w * units, 3) if units is not None and net_w is not None else None,
            "Volume (m³)": volume,
            "Units": units,
            "Stock Real": stock,
            "Insuficiente?": insuf,
            "Falta": falta,
        }

        grouped.setdefault(subcategory, []).append(product_data)

    for subcat, products in grouped.items():
        grouped[subcat] = sorted(products, key=lambda x: x.get("SKU") or "")
        
    # Build final output list
    output = []
    for subcat, products in grouped.items():
        # Add subcategory header
        output.append({
            "Product": f"——— {subcat} ———",
            "SKU": "",
            "Net Weight (kg)": "",
            "Total Weight (kg)": "",
            "Volume (m³)": "",
            "Units": "",
            "Stock Real": "",
            "Insuficiente?": "",
            "Falta": "",
        })

        output.extend(products)

        # Ensure numeric conversion for subtotal
        subtotal_df = pd.DataFrame(products)
        for col in ["Total Weight (kg)", "Volume (m³)", "Units", "Falta"]:
            subtotal_df[col] = pd.to_numeric(subtotal_df[col], errors="coerce")

        num_falta = subtotal_df['Falta'].sum(min_count=1)
        num_falta = 0 if pd.isna(num_falta) else num_falta
        
       # Compute each subtotal
        sum_weight = round(subtotal_df["Total Weight (kg)"].sum(min_count=1), 2)
        sum_volume = round(subtotal_df["Volume (m³)"].sum(min_count=1), 5)
        sum_units  = round(subtotal_df["Units"].sum(min_count=1), 1)

        # Add a subtotal row that only fills the new Subtotal >… columns
        output.append({
            "Product": f"                                    Subtotal {subcat}",
            "SKU": "",
            "Net Weight (kg)": "",
            "Total Weight (kg)": "",
            "Subtotal > Total Weight (kg)": sum_weight,
            "Volume (m³)": "",
            "Subtotal > Volume (m³)":         sum_volume,
            "Units": "",
            "Subtotal > Units":                sum_units,
            "Stock Real": "",
            "Insuficiente?": "",
            "Falta": "",            
            "Subtotal > Falta":                num_falta
        })

    # If no products matched, return empty DataFrame with expected structure
    if not output:
        return pd.DataFrame(columns=[
            "SKU", "Product", "Units", "Net Weight (kg)", "Total Weight (kg)",
            "Volume (m³)", "Stock Real", "Insuficiente?", "Falta"
        ])

    df = pd.DataFrame(output)

    ##################
    
    subtotal_mask = df["Product"].str.contains("Subtotal", na=False)
    subtotal_cols = [
        "Subtotal > Total Weight (kg)",
        "Subtotal > Volume (m³)",
        "Subtotal > Units",
        "Subtotal > Falta"
    ]

    # for those rows only, replace any missing with 0
    for col in subtotal_cols:
        df.loc[subtotal_mask, col] = df.loc[subtotal_mask, col].fillna(0)




    
    # Ensure consistent column order
    expected_cols = [
        "SKU","Product", "Units", "Subtotal > Units" ,"Net Weight (kg)","Total Weight (kg)", "Subtotal > Total Weight (kg)",
        "Volume (m³)", "Subtotal > Volume (m³)","Stock Real","Insuficiente?","Falta", "Subtotal > Falta"
    ]
    for col in expected_cols:
        if col not in df.columns:
            df[col] = None
    df = df[expected_cols]

    return df

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
                    st.warning("No valid products found in the selected presupuesto. They may lack SKUs or attribute data.")
                else:
                    st.success(f"Presupuesto '{original_docnum}' details loaded!")
                    # Convert numeric columns safely
                    for col in ["Net Weight (kg)", "Total Weight (kg)", "Volume (m³)", "Units", "Stock Real", "Falta"]:
                        df_result[col] = pd.to_numeric(df_result[col], errors='coerce')
                    
                    # Style function for subcategory header rows
                    def highlight_subcategories(row):
                        if str(row['Product']).startswith('——'):
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
                            "Net Weight (kg)": "{:.2f}",
                            "Total Weight (kg)": "{:.2f}",
                            "Subtotal > Total Weight (kg)": "{:.2f}",
                            "Volume (m³)": "{:.3f}",
                            "Subtotal > Volume (m³)": "{:.3f}",
                            "Units": "{:,.0f}",
                            "Subtotal > Units":    "{:,.0f}",
                            "Stock Real": "{:,.0f}",
                            "Falta": "{:,.0f}",
                            "Subtotal > Falta":   "{:,.0f}",  
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
                    
                        pallets_by_weight = round(total_weight / 1300, 3)
                        pallets_by_volume = round(total_volume / 2, 3)
                        estimated_pallets = int(np.ceil(max(pallets_by_weight, pallets_by_volume)))
                        if estimated_pallets == 0:
                            estimated_pallets = 1
                        
                        # Build a single‐row summary DataFrame
                        summary_df = pd.DataFrame([{
                            "Total Units": int(total_units),
                            "Total Weight (kg)": f"{total_weight:.2f} kg",
                            "Total Volume (m³)": f"{total_volume:.3f} m³",
                            "Pallets by Weight": pallets_by_weight,
                            "Pallets by Volume": pallets_by_volume,
                            "Pallets Needed": estimated_pallets
                        }])
                    
                        st.subheader("📊 Estimated Pallet Summary")
                        st.dataframe(summary_df)

                    filename=f"{original_docnum}_stock.xlsx"
                    excel_buffer = io.BytesIO()
                    with pd.ExcelWriter(excel_buffer, engine='openpyxl') as writer:
                        df_result.to_excel(writer, index=False, sheet_name='Sheet1')
                    excel_buffer.seek(0)
                
                    # Download button
                    st.download_button(
                        label="📥 Download Excel (Stock)",
                        data=excel_buffer,
                        file_name=filename,
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                    )
                    
        
                    filename=f"{original_docnum}_pallets.xlsx"
                    excel_buffer = io.BytesIO()
                    
                    with pd.ExcelWriter(excel_buffer, engine='openpyxl') as writer:
                        summary_df.to_excel(writer, index=False, sheet_name='Sheet1')
                    excel_buffer.seek(0)
                    
                        # Download button
                    st.download_button(
                        label="📥 Download Excel (Pallets)",
                        data=excel_buffer,
                        file_name=filename,
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                    )
                    
        except Exception as e:
            st.error(f"Something went wrong: {e}")

