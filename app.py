import os
import re
import sqlite3
import time
import unicodedata
import urllib.request
from io import BytesIO

import numpy as np
import pandas as pd
import plotly.express as px
import streamlit as st
from sklearn.metrics.pairwise import cosine_similarity

try:
    from pymarc.marcxml import parse_xml_to_array
    PYMARC_OK = True
except ImportError:
    PYMARC_OK = False

# ==========================================
# CONFIGURACIÓN DE PÁGINA
# ==========================================
st.set_page_config(page_title="Gestión de la Colección", page_icon="📚", layout="wide")

ANIO_ACTUAL = 2026
VIDEOS_DIR = os.path.join(os.path.dirname(__file__), "videos")
VIDEOS = {
    "topo": os.path.join(VIDEOS_DIR, "topografico.mp4"),
    "catalogo": os.path.join(VIDEOS_DIR, "catalogo.mp4"),
    "nunca": os.path.join(VIDEOS_DIR, "no_prestados.mp4"),
    "mas2": os.path.join(VIDEOS_DIR, "mas_prestados.mp4"),
}

# ==========================================
# ESTILOS: estética "ficha de biblioteca" (inspirada en la versión Render)
# ==========================================
st.markdown("""
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,400;9..144,600;9..144,700&family=Public+Sans:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500;600&display=swap" rel="stylesheet">
<style>
:root {
    --paper: #EDE6D6;
    --paper-card: #F7F3E8;
    --ink: #1F2A24;
    --ink-soft: #4A5248;
    --spine-green: #2F5233;
    --spine-green-dark: #203a24;
    --stamp-red: #A23B2E;
    --rule: #C9BFA4;
    --gold: #B08D3E;
    --font-display: "Fraunces", serif;
    --font-body: "Public Sans", sans-serif;
    --font-mono: "IBM Plex Mono", monospace;
}
.stApp { background-color: var(--paper); font-family: var(--font-body); color: var(--ink); }
section[data-testid="stSidebar"] { background-color: var(--paper-card); border-right: 1px solid var(--rule); }
h1, h2, h3 { font-family: var(--font-display) !important; color: var(--spine-green-dark) !important; }
.main-title { font-size: 2.3rem; color: var(--spine-green-dark); font-weight: 700; margin-bottom: 0.2rem; font-family: var(--font-display); }
.subtitle { font-size: 1.05rem; color: var(--ink-soft); margin-bottom: 1.6rem; }
.ledger-tab {
    display: inline-block; background: var(--spine-green); color: var(--paper-card);
    font-family: var(--font-mono); font-size: 0.72rem; letter-spacing: 0.08em; text-transform: uppercase;
    padding: 0.15rem 0.6rem; border-radius: 3px 3px 0 0; margin-bottom: -1px;
}
div[data-testid="stMetric"] {
    background-color: var(--paper-card); border: 1px solid var(--rule); border-radius: 0.4rem;
    padding: 0.9rem 1rem; box-shadow: 0 1px 2px rgba(0,0,0,0.06);
}
div[data-testid="stMetricLabel"] { color: var(--ink-soft) !important; font-size: 0.75rem !important; text-transform: uppercase; letter-spacing: 0.04em; }
div[data-testid="stMetricValue"] { color: var(--spine-green-dark) !important; font-family: var(--font-display) !important; }
.stButton > button[kind="primary"], .stButton > button:has(div:contains("Analizar")) {
    background-color: var(--stamp-red); border-color: var(--stamp-red);
}
.stButton > button { border-radius: 4px; font-family: var(--font-body); }
.stTabs [data-baseweb="tab"] { font-family: var(--font-mono); font-size: 0.85rem; color: var(--ink-soft); }
.stTabs [aria-selected="true"] { color: var(--spine-green-dark) !important; font-weight: 600; }
.isbd-card {
    background: var(--paper-card); border: 1px solid var(--rule); border-left: 4px solid var(--gold);
    border-radius: 4px; padding: 1rem 1.2rem; font-family: var(--font-body); margin-bottom: 0.8rem;
}
.isbd-signatura { font-family: var(--font-mono); color: var(--spine-green-dark); font-weight: 600; margin-bottom: 0.3rem; }
.isbd-autor { font-weight: 600; margin-bottom: 0.2rem; }
.isbd-parrafo { margin-bottom: 0.5rem; }
.isbd-materias { color: var(--ink-soft); font-size: 0.9rem; margin-bottom: 0.4rem; }
.isbd-isbn { font-family: var(--font-mono); font-size: 0.82rem; color: var(--ink-soft); }
.isbd-nota { font-size: 0.75rem; color: var(--ink-soft); font-style: italic; margin-top: 0.5rem; }
.huerfanos-note { font-size: 0.82rem; color: var(--ink-soft); }
.req-badge { color: var(--stamp-red); font-size: 0.68rem; text-transform: uppercase; font-weight: 600; }
.opt-badge { color: var(--ink-soft); font-size: 0.68rem; text-transform: uppercase; }
</style>
""", unsafe_allow_html=True)

# ==========================================
# INICIALIZACIÓN DE ESTADOS DE SESIÓN
# ==========================================
_DEFAULTS = {
    "analizado": False,
    "resultado": None,       # (df, huerfanos, fichas_catalogo)
    "biblioteca_analisis": None,
}
for _k, _v in _DEFAULTS.items():
    if _k not in st.session_state:
        st.session_state[_k] = _v

# ==========================================
# CONFIGURACIÓN DE BASE DE DATOS (NO TOCAR DB_URL)
# ==========================================
DB_PATH = "gestion_coleccion.db"
# 1. CAMBIO CLAVE: Cambiamos dl=0 por dl=1 al final de la URL para forzar la descarga directa del binario (.db)
DB_URL = "https://www.dropbox.com/scl/fi/zlhw2qkfpebtvzaimxto1/bibliotecas_navarra2.db?rlkey=fg46liauy6omsq3dkz4gnn5pk&st=jr3xe9k4&dl=1"

def asegurar_base_de_datos():
    """Maneja la descarga del archivo en disco.
    Limpia archivos HTML corruptos previos y descarga el archivo SQLite real."""
    debe_descargar = False

    if not os.path.exists(DB_PATH):
        debe_descargar = True
    elif os.path.getsize(DB_PATH) < 10000:
        # Si el archivo mide menos de 10KB, es el texto HTML de la vista previa vieja.
        os.remove(DB_PATH)
        debe_descargar = True

    if debe_descargar:
        with st.spinner("Descargando base de datos de la colección... Esto puede tardar un minuto la primera vez."):
            try:
                urllib.request.urlretrieve(DB_URL, DB_PATH)
                st.toast("¡Base de datos descargada con éxito!", icon="📥")
                return True
            except Exception as e:
                st.error(f"Error crítico al descargar la base de datos desde Dropbox: {e}")
                return False
    return True


@st.cache_resource
def obtener_conexion_db():
    """Cachea la conexión SQLite, registra REGEXP y crea índices de apoyo
    (igual que en la versión Render) para que las consultas de recomendaciones
    no acaben haciendo un full table scan sobre el catálogo completo de la red."""
    try:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        conn.create_function(
            "REGEXP", 2,
            lambda expr, item: bool(re.search(expr, str(item), re.IGNORECASE)) if item else False,
        )
        try:
            conn.execute("CREATE INDEX IF NOT EXISTS idx_ejemplares_id_sistema ON ejemplares(id_sistema)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_ejemplares_biblioteca ON ejemplares(biblioteca)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_libros_id_sistema ON libros(id_sistema)")
            conn.commit()
        except Exception:
            pass  # si la tabla aún no existe con ese nombre, seguimos sin índices
        return conn
    except Exception:
        return None


if asegurar_base_de_datos():
    conn = obtener_conexion_db()
    if conn is None:
        st.error("Error al conectar con el archivo SQLite.")
else:
    conn = None


@st.cache_data(ttl=3600)
def tabla_existe(_conn, nombre: str) -> bool:
    try:
        cur = _conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (nombre,))
        return cur.fetchone() is not None
    except Exception:
        return False

# ==========================================
# DECODIFICACIÓN Y PARSEO DE FICHEROS (idéntico a la versión Render, más robusto
# que un simple .decode('utf-8', errors='replace'), que rompía tildes y "ñ")
# ==========================================
def _decodificar_bytes(data: bytes) -> str:
    """Los .txt exportados por AbsysNet vienen casi siempre en Windows-1252/Latin-1.
    Probamos UTF-8 estricto primero y, si falla, caemos a cp1252."""
    if not data:
        return ""
    try:
        texto = data.decode("utf-8")
    except UnicodeDecodeError:
        texto = data.decode("cp1252", errors="replace")
    return texto.replace("\r\n", "\n").replace("\r", "\n")


_RE_AUTOR_PERSONA = re.compile(r"^[A-ZÁÉÍÓÚÑÜ][A-ZÁÉÍÓÚÑÜ.\-\s]*,\s+\S")
_RE_NOTA_ISBN = re.compile(r"^(D\.?L\.?|ISBN)", re.IGNORECASE)


def _parsear_fichas_catalogo(cat_text: str) -> dict:
    """Extrae una ficha ISBD simplificada (autor, título, ISBN, materias) por
    registro a partir del fichero de 'Catálogo' (Formato 1 / Cuerpo 1 / Orden 8)."""
    cat_text_limpio = re.sub(
        r"^\s*(Pág\.\s*\d+|Catálogo Topográfico.*|\d{2}/\d{2}/\d{4})\s*$",
        "", cat_text, flags=re.MULTILINE,
    )
    matches = list(re.finditer(r"^[ \t]*(\d{7,})[ \t]*$", cat_text_limpio, re.MULTILINE))
    fichas = {}
    for i, m in enumerate(matches):
        rid = int(m.group(1))
        start = m.end()
        end = matches[i + 1].start() if i < len(matches) - 1 else len(cat_text_limpio)
        bloque = cat_text_limpio[start:end]

        cierre = re.search(r"^\s*\d{2}\s+[A-ZÁÉÍÓÚÑÜ]{2}\b.*$", bloque, re.MULTILINE)
        if cierre:
            bloque = bloque[: cierre.start()]

        lineas = [ln.strip() for ln in bloque.split("\n") if ln.strip()]
        if not lineas:
            continue

        autor = None
        resto = lineas
        if _RE_AUTOR_PERSONA.match(lineas[0]) and "/" not in lineas[0]:
            autor = lineas[0]
            resto = lineas[1:]
        if not resto:
            continue

        idx_isbn = next((i2 for i2, ln in enumerate(resto) if _RE_NOTA_ISBN.match(ln)), None)
        if idx_isbn is not None:
            titulo_lineas = resto[:idx_isbn]
            linea_isbn = resto[idx_isbn]
            materias_lineas = resto[idx_isbn + 1:]
        else:
            if resto[-1].lstrip().startswith("1."):
                titulo_lineas, linea_isbn, materias_lineas = resto[:-1], "", [resto[-1]]
            else:
                titulo_lineas, linea_isbn, materias_lineas = resto, "", []

        titulo_par = re.sub(r"\s+", " ", " ".join(titulo_lineas)).strip()
        partes_titulo = re.split(r"\s+/\s*", titulo_par, maxsplit=1)
        titulo = partes_titulo[0].strip(" .") or None
        resto_isbd = partes_titulo[1].strip() if len(partes_titulo) > 1 else None

        isbn_m = re.search(r"ISBN\s*([\dXx\-]{8,})", linea_isbn)
        isbn = isbn_m.group(1) if isbn_m else None

        materias_txt = re.sub(r"\s+", " ", " ".join(materias_lineas)).strip()
        corte = re.search(r"\b[IVXLC]+\.\s", materias_txt)
        if corte:
            materias_txt = materias_txt[: corte.start()]
        materias = [
            frag.strip(" .-")
            for frag in re.split(r"\d+\.\s*", materias_txt)
            if frag.strip(" .-")
        ]

        fichas[rid] = {"autor": autor, "titulo": titulo, "resto_isbd": resto_isbd, "isbn": isbn, "materias": materias}
    return fichas


def _parsear_linea_topo(line: str):
    """Divide una línea del listado topográfico por las columnas de ancho fijo
    que usa AbsysNet (cortar por posición, no por regex, evita mezclar
    'Signatura' con 'Sig. supl.' cuando hay géneros como 'Histórica')."""
    def col(a, b=None):
        return (line[a:b] if b is not None else line[a:]).strip()

    return {
        "signatura": col(0, 27),
        "sig_supl": col(27, 40),
        "cod_bar": col(53, 64),
        "nreg": col(64, 74),
        "titulo": col(74),
    }


@st.cache_data(show_spinner=False)
def procesar_datos(topo_bytes, nunca_bytes, mas2_bytes, catalogo_bytes, tipo_analisis, num_caracteres):
    if not topo_bytes or not catalogo_bytes:
        return None, 0, {}

    topo_text = _decodificar_bytes(topo_bytes)
    data = []
    for line in topo_text.split("\n"):
        line_sin_salto = line.rstrip("\n")
        cabecera = line_sin_salto.strip()
        if not cabecera or re.search(r"^(\d{2}/\d{2}/\d{4}|LISTADO|Signatura|-----)", cabecera):
            continue
        campos = _parsear_linea_topo(line_sin_salto)
        cod_bar = campos["cod_bar"]
        if not re.fullmatch(r"\d{6,}", cod_bar):
            continue
        record_id = int(cod_bar)
        signatura = campos["signatura"]
        titulo = campos["titulo"].rstrip(" /") or "Título no detectado"
        data.append({"record_id": record_id, "signatura_real": signatura, "sig_supl": campos["sig_supl"], "titulo": titulo})

    df_topo = pd.DataFrame(data).drop_duplicates(subset=["record_id"])
    if df_topo.empty:
        return None, 0, {}

    cat_text = _decodificar_bytes(catalogo_bytes)
    cat_text_sin_fechas = re.sub(r"\b\d{2}/\d{2}/\d{4}\b", "", cat_text)
    year_dict = {}
    matches = list(re.finditer(r"^[ \t]*(\d{7,})[ \t]*$", cat_text_sin_fechas, re.MULTILINE))
    for i, m in enumerate(matches):
        rid = int(m.group(1))
        start = m.start()
        end = matches[i + 1].start() if i < len(matches) - 1 else len(cat_text_sin_fechas)
        block = cat_text_sin_fechas[start:end]
        years = re.findall(r"\b(18\d{2}|19\d{2}|20\d{2})\b", block)
        years = [int(y) for y in years if 1800 <= int(y) <= ANIO_ACTUAL]
        if years:
            year_dict[rid] = max(years)

    df_final = df_topo[df_topo["record_id"].isin(year_dict.keys())].copy()
    df_final["year"] = df_final["record_id"].map(year_dict)
    df_final["prestamos"] = 1

    if nunca_bytes:
        nunca_text = _decodificar_bytes(nunca_bytes)
        nunca_ids = {int(x) for x in re.findall(r"\b\d{7,}\b", nunca_text)}
        df_final.loc[df_final["record_id"].isin(nunca_ids), "prestamos"] = 0

    if mas2_bytes:
        mas2_text = _decodificar_bytes(mas2_bytes)
        mas2_ids = {int(x) for x in re.findall(r"\b\d{7,}\b", mas2_text)}
        df_final.loc[df_final["record_id"].isin(mas2_ids), "prestamos"] = 2

    df_final["prestado"] = df_final["prestamos"] > 0

    def clasificar_dinamico(sign):
        if not sign or not isinstance(sign, str):
            return "Sin clasificar"
        s = sign.strip().upper()
        if tipo_analisis == "Clasificación Mixta Estándar (CDU + Letras)":
            if re.search(r"\bI\s+DVD\b", s):
                return "I DVD (DVD Infantil)"
            if re.search(r"\bDVD\b", s):
                return "DVD Audiovisual"
            if re.search(r"^IC\b", s):
                return "IC (Comic Infantil)"
            if re.search(r"^C\b", s):
                return "C (Comic Adultos)"
            if re.search(r"\bIP\b", s):
                return "IP (Infantil Poesía)"
            if re.search(r"\bIT\b", s):
                return "IT (Infantil Teatro)"
            if re.search(r"^I\s+[12356789]", s):
                return "CDU Infantil"
            match_inf = re.match(r"^(I[0-3])", s)
            if match_inf:
                return f"{match_inf.group(1)} (Infantil)"
            if re.search(r"\bJN\b", s):
                return "JN (Juvenil)"
            if re.search(r"\bN\s", s):
                return "Ficción / Narrativa"
            if re.search(r"\bP\s", s):
                return "Poesía"
            if re.search(r"\bT\s", s):
                return "Teatro"
            m = re.match(r"^(\d)", s)
            if m:
                cats = {
                    "0": "0 - Generalidades", "1": "1 - Filosofía", "2": "2 - Religión",
                    "3": "3 - Ciencias Sociales", "4": "4 - Lingüística",
                    "5": "5 - Ciencias Puras", "6": "6 - Tecnología",
                    "7": "7 - Arte / Deportes", "8": "8 - Literatura",
                    "9": "9 - Historia / Geografía",
                }
                return cats.get(m.group(1), f"CDU {m.group(1)}xx")
            return "Otros"
        elif tipo_analisis == "Solo Dígitos Iniciales de la CDU":
            m = re.match(r"^(\d+)", s)
            return f"CDU {m.group(1)[0]}" if m else "Ficción / Otros"
        elif tipo_analisis == "Longitud Fija (Primeros caracteres)":
            return s[:num_caracteres]
        return "Otros"

    df_final["categoria"] = df_final["signatura_real"].apply(clasificar_dinamico)

    fichas_catalogo = _parsear_fichas_catalogo(cat_text)
    ids_validos = set(df_final["record_id"])
    fichas_filtradas = {rid: f for rid, f in fichas_catalogo.items() if rid in ids_validos}

    df_final["autor"] = df_final["record_id"].map(lambda rid: (fichas_filtradas.get(rid) or {}).get("autor") or "")
    df_final["materias_texto"] = df_final["record_id"].map(
        lambda rid: " | ".join((fichas_filtradas.get(rid) or {}).get("materias") or [])
    )
    titulo_catalogo = df_final["record_id"].map(lambda rid: (fichas_filtradas.get(rid) or {}).get("titulo"))
    df_final["titulo"] = titulo_catalogo.where(titulo_catalogo.notna(), df_final["titulo"])

    return df_final, (len(df_topo) - len(df_final)), fichas_filtradas


def _clasificar_macro(cat: str) -> str:
    c = str(cat).strip().upper()
    if "DVD" in c or "AUDIOVISUAL" in c or "CD" in c:
        return "Audiovisuales"
    if re.match(r"^(I|JN|IC|IP|IT|INFANTIL|JUVENIL)(\s|\d+|-|$)", c):
        return "Infantil/Juvenil"
    return "Adultos"


def _es_categoria_infantil(categoria) -> bool:
    cat_str = str(categoria).upper()
    if "INFANTIL" in cat_str or "JUVENIL" in cat_str:
        return True
    if re.match(r"^(I[0-9]?|JN|IC|IP|IT)(\s|$)", cat_str):
        return True
    return False


def _extraer_raiz(sig) -> str:
    s = str(sig).strip().upper()
    m = re.match(r"^([A-Z]*\s*\d{2})", s)
    if m:
        return m.group(1)
    return s.split()[0][:3] if s.split() else s


def _sin_acentos(texto: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", str(texto)) if not unicodedata.combining(c))


def _wildcard_mask(series: pd.Series, patron: str) -> pd.Series:
    serie_norm = series.astype(str).str.upper().str.strip()
    if "*" in patron:
        regex_patron = re.escape(patron).replace(r"\*", ".*")
        return serie_norm.str.match(regex_patron, na=False)
    return serie_norm.str.startswith(patron, na=False)


def _wildcard_contains_mask(series: pd.Series, patron: str) -> pd.Series:
    serie_norm = series.astype(str).map(_sin_acentos).str.upper()
    patron_norm = _sin_acentos(patron)
    regex_patron = re.escape(patron_norm).replace(r"\*", ".*")
    return serie_norm.str.contains(regex_patron, na=False, regex=True)


def _texto_contains_mask(series: pd.Series, busqueda: str) -> pd.Series:
    busqueda = busqueda.strip().upper()
    if "*" in busqueda:
        return _wildcard_contains_mask(series, busqueda)
    serie_norm = series.astype(str).map(_sin_acentos).str.upper()
    return serie_norm.str.contains(re.escape(_sin_acentos(busqueda)), na=False)


CAMPOS_BUSQUEDA_SIGNATURA = {"signatura": "signatura_real", "titulo": "titulo", "autor": "autor", "materia": "materias_texto"}

# ==========================================
# RECOMENDACIONES SOBRE LA RED (usan la BD completa)
# ==========================================
def obtener_recomendaciones_automaticas(conexion, biblioteca, limite=50):
    query = """
        SELECT l.id_sistema, l.titulo, l.autor, l.anio, COUNT(DISTINCT e.biblioteca) as total_bibliotecas
        FROM libros l
        JOIN ejemplares e ON l.id_sistema = e.id_sistema
        WHERE NOT EXISTS (
            SELECT 1 FROM ejemplares e2
            WHERE e2.id_sistema = l.id_sistema AND TRIM(UPPER(e2.biblioteca)) = ?
        )
        GROUP BY l.id_sistema, l.titulo, l.autor, l.anio
        ORDER BY total_bibliotecas DESC
        LIMIT ?
    """
    try:
        params = [biblioteca.upper().strip(), int(limite)]
        return pd.read_sql_query(query, conexion, params=params)
    except Exception as e:
        st.error(f"❌ Error en la consulta SQL: {e}")
        return pd.DataFrame()


MENUS_ADULTOS = {
    "Ficción": "📖 Ficción Adultos (821)", "CDU 0": "📂 CDU 0 · Generalidades",
    "CDU 1": "📂 CDU 1 · Filosofía / Psicología", "CDU 2": "📂 CDU 2 · Religión / Teología",
    "CDU 3": "📂 CDU 3 · Ciencias Sociales / Economía", "CDU 5": "📂 CDU 5 · Ciencias Puras / Naturales",
    "CDU 6": "📂 CDU 6 · Ciencias Aplicadas / Tecnología", "CDU 7": "📂 CDU 7 · Bellas Artes / Deportes",
    "CDU 8": "📂 CDU 8 · Lingüística / Literatura", "CDU 9": "📂 CDU 9 · Geografía / Historia",
}
MENUS_INFANTIL = {
    "I0": "👶 I0 · Bebeteca", "I1": "🧸 I1 · Hasta 8 años", "I2": "🎒 I2 · 8 a 10 años",
    "I3": "🛡️ I3 · 10 a 12 años", "JN": "⚡ JN · Juvenil",
    "I CDU 0": "📚 I CDU 0 · Generalidades", "I CDU 1": "📚 I CDU 1 · Filosofía", "I CDU 2": "📚 I CDU 2 · Religión",
    "I CDU 3": "📚 I CDU 3 · Ciencias Sociales", "I CDU 4": "📚 I CDU 4 · Lengua", "I CDU 5": "📚 I CDU 5 · Ciencias Puras",
    "I CDU 6": "📚 I CDU 6 · Ciencias Aplicadas", "I CDU 7": "📚 I CDU 7 · Arte / Deportes",
    "I CDU 8": "📚 I CDU 8 · Literatura", "I CDU 9": "📚 I CDU 9 · Geografía e Historia",
}


def _clasificar_infantil(todas_sigs):
    if not todas_sigs:
        return None
    sigs = [x.strip().upper() for x in str(todas_sigs).split("||") if x.strip()]
    for sig in sigs:
        m = re.search(r"\b(I0|I1|I2|I3|JN)\b", sig)
        if m:
            return m.group(1)
        m2 = re.search(r"\bI\s+([0-9])\b", sig)
        if m2:
            return f"I CDU {m2.group(1)}"
        m3 = re.search(r"\bI([4-9])\b", sig)
        if m3:
            return f"I CDU {m3.group(1)}"
    return None


def _clasificar_libro_cdu(cdu: str, todas_signaturas: str):
    cdu = str(cdu).strip().upper()
    if cdu.startswith("087.5"):
        cat_inf = _clasificar_infantil(todas_signaturas)
        if cat_inf:
            return "Infantil", cat_inf
        return None, None
    if cdu.startswith("821"):
        return "Adultos", "Ficción"
    m = re.match(r"^(\d)", cdu)
    if m and m.group(1) in ["0", "1", "2", "3", "5", "6", "7", "8", "9"]:
        return "Adultos", f"CDU {m.group(1)}"
    return None, None


def _ids_con_materia(conn, ids_sistema: list, busqueda: str) -> set:
    """Subconjunto de id_sistema cuya tabla `materias` contiene el texto buscado."""
    if not ids_sistema:
        return set()
    try:
        placeholders = ",".join("?" * len(ids_sistema))
        filas = conn.execute(
            f"SELECT id_sistema, materia FROM materias WHERE id_sistema IN ({placeholders})", ids_sistema,
        ).fetchall()
    except sqlite3.OperationalError:
        return set()
    busqueda_norm = _sin_acentos(busqueda.upper())
    return {id_sistema for id_sistema, materia in filas if materia and busqueda_norm in _sin_acentos(materia.upper())}


@st.cache_data(ttl=1800, show_spinner=False)
def cargar_base_recomendaciones_cdu(_conn, biblioteca: str, anio_minimo: int):
    query_cdu = """
        SELECT l.id_sistema, l.titulo, l.autor, l.anio, l.cdu,
               COUNT(DISTINCT e.biblioteca) AS id_red_bibliotecas,
               GROUP_CONCAT(e.signatura, '||') AS todas_signaturas
        FROM libros l
        JOIN ejemplares e ON l.id_sistema = e.id_sistema
        WHERE l.id_sistema NOT IN (
            SELECT DISTINCT id_sistema FROM ejemplares WHERE UPPER(TRIM(biblioteca)) = ?
        )
        AND CAST(COALESCE(l.anio, 0) AS INTEGER) >= ?
        GROUP BY l.id_sistema, l.titulo, l.autor, l.anio, l.cdu
        HAVING id_red_bibliotecas > 0
    """
    return pd.read_sql_query(query_cdu, _conn, params=[biblioteca.upper().strip(), int(anio_minimo)])


# ==========================================
# FICHA CATALOGRÁFICA
# ==========================================
CAMPOS_FICHA = {"250": "Edición", "300": "Descripción física", "490": "Serie", "500": "Notas", "505": "Contenido", "520": "Resumen"}


def render_isbd_html(signatura, autor, titulo, resto_isbd, materias, isbn, nota):
    materias_txt = " ".join(f"{i+1}. {m}." for i, m in enumerate(materias or []))
    parrafo = f"<span>{titulo or 'Título no disponible'}</span>"
    if autor:
        parrafo += f" / {autor}"
    if resto_isbd:
        parrafo += f" {resto_isbd}"
    html = f"""
    <div class="isbd-card">
        {f'<div class="isbd-signatura">{signatura}</div>' if signatura else ''}
        {f'<div class="isbd-autor">{autor}</div>' if autor else ''}
        <div class="isbd-parrafo">{parrafo}</div>
        {f'<div class="isbd-materias">{materias_txt}</div>' if materias_txt else ''}
        {f'<div class="isbd-isbn">ISBN {isbn}</div>' if isbn else ''}
        <p class="isbd-nota">{nota}</p>
    </div>
    """
    return html


def obtener_ficha_red(conexion, id_sistema):
    """Ficha catalográfica completa desde la BD de la red (registro MARC si está
    disponible; si no, se recurre a los datos básicos de 'libros')."""
    ejemplares = []
    try:
        filas = conexion.execute(
            "SELECT biblioteca, seccion, signatura, codigo_barras FROM ejemplares WHERE id_sistema = ?", (id_sistema,)
        ).fetchall()
        ejemplares = [{"biblioteca": r[0], "seccion": r[1], "signatura": r[2], "codigo_barras": r[3]} for r in filas]
    except Exception:
        pass

    if PYMARC_OK and tabla_existe(conexion, "marc_completo"):
        try:
            row = conexion.execute("SELECT marcxml FROM marc_completo WHERE id_sistema = ?", (id_sistema,)).fetchone()
            if row:
                xml_envuelto = (
                    b'<?xml version="1.0" encoding="UTF-8"?>'
                    b'<collection xmlns="http://www.loc.gov/MARC21/slim">' + row[0].encode("utf-8") + b"</collection>"
                )
                registros = parse_xml_to_array(BytesIO(xml_envuelto))
                if registros:
                    record = registros[0]
                    campos_extra = {}
                    for tag, etiqueta in CAMPOS_FICHA.items():
                        valores = [c.format_field().strip() for c in record.get_fields(tag)]
                        if valores:
                            campos_extra[etiqueta] = valores
                    materias = [c.format_field().strip() for c in record.get_fields("650")]
                    campo_260 = record["260"] if "260" in record else None
                    return {
                        "titulo": record.title, "autor": record.author, "isbn": record.isbn,
                        "cdu": record["080"]["a"] if "080" in record and "a" in record["080"] else None,
                        "editorial": campo_260["b"] if campo_260 and "b" in campo_260 else None,
                        "anio": campo_260["c"] if campo_260 and "c" in campo_260 else None,
                        "edicion": campos_extra.get("Edición", [None])[0],
                        "materias": materias, "ejemplares": ejemplares, "fuente": "marc",
                    }
        except Exception:
            pass

    # Fallback: datos básicos de la tabla libros
    try:
        row = conexion.execute("SELECT titulo, autor, anio, cdu FROM libros WHERE id_sistema = ?", (id_sistema,)).fetchone()
        if row:
            return {"titulo": row[0], "autor": row[1], "anio": row[2], "cdu": row[3], "materias": [], "ejemplares": ejemplares, "fuente": "basico"}
    except Exception:
        pass
    return {"titulo": None, "autor": None, "materias": [], "ejemplares": ejemplares, "fuente": "ninguno"}


def obtener_ficha_sesion(id_sistema):
    """Ficha catalográfica reconstruida a partir del propio fichero de catálogo
    subido por el usuario (sin conexión a la red)."""
    df, _, fichas = st.session_state["resultado"]
    fila = df[df["record_id"] == id_sistema]
    if fila.empty:
        return None
    row = fila.iloc[0]
    f = fichas.get(id_sistema, {})
    return {
        "titulo": row["titulo"], "autor": f.get("autor") or None, "signatura": row["signatura_real"],
        "cdu": row["categoria"], "anio": None if pd.isna(row["year"]) else int(row["year"]),
        "isbn": f.get("isbn"), "detalle_isbd": f.get("resto_isbd"), "materias": f.get("materias") or [],
    }


def _mostrar_contenido_ficha_sesion(id_sistema):
    f = obtener_ficha_sesion(id_sistema)
    if not f:
        st.warning("Ese registro no está en el análisis actual.")
        return
    st.markdown(
        render_isbd_html(
            f["signatura"], f["autor"], f["titulo"], f.get("detalle_isbd"), f["materias"], f["isbn"],
            "Ficha generada a partir del catálogo subido para este análisis.",
        ),
        unsafe_allow_html=True,
    )


def _mostrar_contenido_ficha_red(id_sistema):
    if conn is None:
        st.warning("No hay conexión activa con la base de datos de la red.")
        return
    f = obtener_ficha_red(conn, id_sistema)
    tab_ficha, tab_sucursales = st.tabs(["📇 Ficha", "🏛️ Sucursales"])
    with tab_ficha:
        resto = f.get("editorial") and f"{f.get('editorial')}, {f.get('anio') or ''}".strip(", ")
        st.markdown(
            render_isbd_html(
                f.get("cdu"), f.get("autor"), f.get("titulo"), resto, f.get("materias"), f.get("isbn"),
                "Ficha generada automáticamente a partir del registro de la red." if f.get("fuente") != "ninguno"
                else "No se encontró información detallada para este registro.",
            ),
            unsafe_allow_html=True,
        )
    with tab_sucursales:
        ejemplares = f.get("ejemplares") or []
        if ejemplares:
            st.dataframe(pd.DataFrame(ejemplares), use_container_width=True, hide_index=True)
        else:
            st.info("Sin ejemplares localizados en la red.")


if hasattr(st, "dialog"):
    @st.dialog("Ficha catalográfica")
    def abrir_ficha_dialogo(id_sistema, origen):
        if origen == "sesion":
            _mostrar_contenido_ficha_sesion(id_sistema)
        else:
            _mostrar_contenido_ficha_red(id_sistema)
else:
    def abrir_ficha_dialogo(id_sistema, origen):
        with st.expander(f"📇 Ficha catalográfica — {id_sistema}", expanded=True):
            if origen == "sesion":
                _mostrar_contenido_ficha_sesion(id_sistema)
            else:
                _mostrar_contenido_ficha_red(id_sistema)


def tabla_con_ficha(df_mostrar, key, id_col="id_sistema", origen="sesion"):
    """Muestra un dataframe seleccionable; si el usuario marca una fila, ofrece
    un botón para abrir su ficha catalográfica (equivalente a las filas
    clicables de la versión Render)."""
    try:
        evento = st.dataframe(
            df_mostrar, use_container_width=True, hide_index=True,
            on_select="rerun", selection_mode="single-row", key=key,
        )
        filas_sel = evento.selection.rows if hasattr(evento, "selection") else []
    except TypeError:
        # Streamlit demasiado antiguo para on_select: fallback sin selección de filas
        st.dataframe(df_mostrar, use_container_width=True, hide_index=True, key=key)
        filas_sel = []

    if filas_sel:
        idx = filas_sel[0]
        id_sel = df_mostrar.iloc[idx][id_col]
        if st.button(f"📇 Ver ficha catalográfica ({id_sel})", key=f"btn_ficha_{key}"):
            abrir_ficha_dialogo(int(id_sel), origen)


def ayuda_video(clave, etiqueta):
    """Botón de ayuda que muestra el vídeo correspondiente a cómo obtener el fichero."""
    ruta = VIDEOS.get(clave)
    if not ruta or not os.path.exists(ruta):
        return
    if hasattr(st, "popover"):
        with st.popover(f"❓", use_container_width=False):
            st.video(ruta)
    else:
        with st.expander(f"❓"):
            st.video(ruta)

# ==========================================
# BIBLIOTECAS DE LA RED
# ==========================================
BIBLIOTECAS = {
    "Ablitas": 2610, "Aibar / Oibar": 769, "Allo": 988, "Altsasu / Alsasua": 7590, "Andosilla": 2882,
    "Ansoáin / Antsoain": 10608, "Añorbe": 628, "Aoiz, Agoitz": 2970, "Aranguren": 12517, "Arbizu": 1126,
    "Arguedas": 2313, "Arroniz": 1035, "Artajona": 1772, "Artica / Artika": 4848, "Aurizberri / Espinal": 2627,
    "Ayegui, Aiegi": 2531, "Azagra": 3749, "Barañain": 19575, "Baztan": 7831, "Bera": 3792, "Beriáin": 4129,
    "Berriozar": 10919, "Bibliobús": 8700, "Buñuel": 2309, "Burlada / Burlata": 20865, "Cabanillas": 1379,
    "Cadreita": 2186, "Caparroso": 2786, "Cárcar": 1150, "Carcastillo": 2435, "Cascante": 4050, "Cáseda": 969,
    "Castejón": 4435, "Cintruénigo": 8265, "Cirauqui / Zirauki": 467, "Corella": 8629, "Cortes": 3149,
    "Doneztebe / Santesteban": 1858, "Valle de Egües / Egusibar": 22121, "Estella / Lizarra": 14195,
    "Etxarri Aranatz": 2521, "Falces": 2375, "Fitero": 2146, "Fontellas": 1005, "Funes": 2542, "Fustiñana": 2457,
    "Huarte / Uharte": 7562, "Irurtzun": 2316, "Larraga": 2087, "Leitza": 3016, "Lekunberri": 1689,
    "Lerín": 1789, "Lesaka": 2731, "Lodosa": 4894, "Los Arcos": 1151, "Lumbier": 1326, "Mañeru": 445,
    "Marcilla": 2875, "Mélida": 715, "Mendavia": 3496, "Mendigorria": 1191, "Milagro": 3549,
    "Miranda de Arga": 917, "Monteagudo": 1102, "Murchante": 4237, "Noain": 8429, "Obanos": 920,
    "Olazti / Olaztigutía": 1483, "Olite / Erriberri": 4019, "Orkoien": 4051, "Oteiza": 923,
    "Peralta / Azkoien": 5979, "PNA - Biblioteca de Navarra": 208243, "PNA - Civican": 19418,
    "PNA - Echavacoiz": 5447, "PNA - Iturrama": 22354, "PNA - Mendillorri": 18747, "PNA - Milagrosa": 34998,
    "PNA - San Francisco": 25864, "PNA - San Jorge": 22203, "PNA - San Pedro": 26896, "PNA - Txantrea": 20264,
    "PNA - Yamaguchi": 16372, "Puente la Reina / Gares": 2944, "Ribaforada": 3715, "Roncal / Erronkari": 209,
    "San Adrián": 6429, "Sangüesa / Zangoza": 4814, "Sartaguda": 1328, "Sesma": 1226, "Tafalla": 10698,
    "Tudela": 37791, "Ultzama": 1636, "Urdiain": 638, "Valtierra": 2423, "Viana": 4370, "Villafranca": 3004,
    "Villava / Atarrabia": 10067, "Ziorda": 352, "Zizur Mayor / Zizur Nagusia": 15715,
}

# ==========================================
# CABECERA
# ==========================================
st.markdown('<div class="ledger-tab">Ficha 01</div>', unsafe_allow_html=True)
st.markdown('<div class="main-title">📚 Gestión de la Colección</div>', unsafe_allow_html=True)
st.markdown(
    '<div class="subtitle">Fichero analítico del fondo bibliotecario — Red de Bibliotecas Públicas de Navarra</div>',
    unsafe_allow_html=True,
)

# ==========================================
# PANEL LATERAL (SIDEBAR)
# ==========================================
with st.sidebar:
    st.header("🏢 Selección de biblioteca")
    biblioteca_seleccionada = st.selectbox("Centro:", options=sorted(BIBLIOTECAS.keys()))
    poblacion_atendida = BIBLIOTECAS[biblioteca_seleccionada]
    st.caption(f"Población atendida: {poblacion_atendida:,} hab.")

    st.markdown("---")

    if not st.session_state["analizado"]:
        st.header("📂 Carga de listados")
        st.caption("Sube los ficheros exportados del catálogo (.txt)")

        c1, c2 = st.columns([3, 1])
        with c1:
            uploaded_topo = st.file_uploader(
                "Listado topográfico · **requerido**", type=["txt"], key="up_topo",
            )
        with c2:
            st.write("")
            ayuda_video("topo", "Listado topográfico")

        c1, c2 = st.columns([3, 1])
        with c1:
            uploaded_catalogo = st.file_uploader(
                "Catálogo (Formato 1 / Cuerpo 1 / Orden 8) · **requerido**", type=["txt"], key="up_catalogo",
            )
        with c2:
            st.write("")
            ayuda_video("catalogo", "Catálogo")

        c1, c2 = st.columns([3, 1])
        with c1:
            uploaded_nunca = st.file_uploader(
                "Ejemplares nunca prestados · opcional", type=["txt"], key="up_nunca",
            )
        with c2:
            st.write("")
            ayuda_video("nunca", "Nunca prestados")

        c1, c2 = st.columns([3, 1])
        with c1:
            uploaded_mas2 = st.file_uploader(
                "Ejemplares más prestados · opcional", type=["txt"], key="up_mas2",
            )
        with c2:
            st.write("")
            ayuda_video("mas2", "Más prestados")

        st.markdown("---")

        tipo_analisis = "Clasificación Mixta Estándar (CDU + Letras)"
        num_caracteres = 3

        if st.button("🚀 Analizar fondos", type="primary", use_container_width=True):
            if not uploaded_topo or not uploaded_catalogo:
                st.error("⚠️ Sube los archivos requeridos.")
            else:
                with st.spinner("Procesando datos..."):
                    resultado = procesar_datos(
                        uploaded_topo.getvalue(),
                        uploaded_nunca.getvalue() if uploaded_nunca else None,
                        uploaded_mas2.getvalue() if uploaded_mas2 else None,
                        uploaded_catalogo.getvalue(),
                        tipo_analisis,
                        num_caracteres,
                    )
                    if resultado[0] is not None:
                        st.session_state["resultado"] = resultado
                        st.session_state["analizado"] = True
                        st.session_state["biblioteca_analisis"] = biblioteca_seleccionada
                        st.rerun()
                    else:
                        st.error("No se pudieron extraer registros válidos de los archivos subidos.")
    else:
        st.success("✅ Datos cargados en memoria.")
        if st.session_state.get("biblioteca_analisis"):
            st.caption(f"Análisis actual: {st.session_state['biblioteca_analisis']}")
        if st.button("🔄 Cambiar / volver a subir archivos", use_container_width=True):
            st.session_state["analizado"] = False
            st.session_state["resultado"] = None
            st.rerun()

# ==========================================
# PANEL CENTRAL DE RESULTADOS
# ==========================================
if st.session_state["analizado"] and st.session_state["resultado"] is not None:
    df_completo, huerfanos, fichas_sesion = st.session_state["resultado"]
    df_completo = df_completo.copy()

    total_docs = len(df_completo)
    pct_prestados = (df_completo["prestado"].sum() / total_docs * 100) if total_docs > 0 else 0
    edad_media = df_completo["year"].mean()
    docs_por_habitante = total_docs / poblacion_atendida if poblacion_atendida > 0 else 0

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("📖 Total volúmenes", f"{total_docs:,}")
    m2.metric("🪪 Índice de circulación", f"{pct_prestados:.1f}%")
    m3.metric("📅 Edad media del fondo", f"{int(edad_media)}" if not np.isnan(edad_media) else "N/A")
    m4.metric("👥 Docs por habitante", f"{docs_por_habitante:.2f}")

    if huerfanos > 0:
        st.markdown(
            f'<p class="huerfanos-note">ℹ️ Se han omitido {huerfanos} registros del topográfico por incoherencias con el catálogo.</p>',
            unsafe_allow_html=True,
        )

    df_completo["macro_seccion"] = df_completo["categoria"].apply(_clasificar_macro)
    df_completo["es_infantil"] = df_completo["categoria"].apply(_es_categoria_infantil)

    st.markdown("---")

    pestana_analisis, pestana_compras = st.tabs(["📊 1. Análisis de la Colección", "🎯 2. Recomendaciones de Compra"])

    # ==========================================
    # BLOQUE 1: ANÁLISIS DE LA COLECCIÓN
    # ==========================================
    with pestana_analisis:
        subtab_general, subtab_cdu, subtab_signatura = st.tabs(
            ["📈 A) Análisis General", "🗂️ B) Análisis por CDU", "🔎 C) Análisis Profundo por Signatura"]
        )

        # ---------- A) ANÁLISIS GENERAL ----------
        with subtab_general:
            st.subheader("⚖️ Diagnóstico según Pautas Oficiales (IFLA)")

            if poblacion_atendida <= 5000:
                pauta_hab, pauta_min, pauta_max = 2.5, 4000, 5500
            elif poblacion_atendida <= 10000:
                pauta_hab, pauta_min, pauta_max = 2.5, 7000, 12500
            elif poblacion_atendida <= 20000:
                pauta_hab, pauta_min, pauta_max = 2.0, 12500, 20000
            elif poblacion_atendida <= 50000:
                pauta_hab, pauta_min, pauta_max = 2.0, 20000, 65000
            elif poblacion_atendida <= 100000:
                pauta_hab, pauta_min, pauta_max = 1.5, 45000, 80000
            else:
                pauta_hab, pauta_min, pauta_max = 1.5, 80000, 95000

            col_al1, col_al2 = st.columns(2)
            with col_al1:
                if total_docs < 2500:
                    st.error(f"🚨 **Alerta:** suelo mínimo absoluto IFLA es de 2.500 obras. Tienes **{total_docs:,}**.")
                elif total_docs < pauta_min:
                    st.warning(f"⚠️ **Déficit de fondo:** recomendado {pauta_min:,}-{pauta_max:,}. Tienes **{total_docs:,}** u.")
                elif total_docs > pauta_max:
                    st.info(f"ℹ️ **Fondo extenso:** el rango inicial recomendado es {pauta_min:,}-{pauta_max:,}. Tienes **{total_docs:,}** u.")
                else:
                    st.success(f"✅ **Óptimo:** volumen adecuado dentro del rango ({pauta_min:,}-{pauta_max:,}).")
            with col_al2:
                if docs_por_habitante > 3.5:
                    st.warning(f"⚠️ **Colección demasiado grande:** {docs_por_habitante:.2f} libros/persona (óptimo {pauta_hab}, máx. sugerido 3.5).")
                elif docs_por_habitante < pauta_hab:
                    st.warning(f"⚠️ **Ratio bajo:** {docs_por_habitante:.2f} doc/hab. (mínimo recomendado {pauta_hab}).")
                else:
                    st.success(f"✅ **Ratio óptimo:** {docs_por_habitante:.2f} doc/hab.")

            st.write("#### 📊 Distribución macroscópica")
            macro_counts = df_completo["macro_seccion"].value_counts()
            tabla_macro = pd.DataFrame({
                "Sección": ["Adultos", "Infantil/Juvenil", "Audiovisuales"],
                "Distribución": [
                    f"{(macro_counts.get(n, 0) / total_docs * 100):.1f}%" if total_docs else "0.0%"
                    for n in ["Adultos", "Infantil/Juvenil", "Audiovisuales"]
                ],
            })
            st.dataframe(tabla_macro, use_container_width=True, hide_index=True)

            st.write("#### 📈 Nivel de rotación física")
            status_map = {0: "Nunca prestado", 1: "Prestado", 2: "Muy prestado"}
            status_counts = df_completo["prestamos"].map(status_map).value_counts().reset_index()
            status_counts.columns = ["Estado", "Cantidad"]
            fig_pie = px.pie(
                status_counts, values="Cantidad", names="Estado", hole=0.4,
                color_discrete_sequence=["#2F5233", "#B08D3E", "#A23B2E"],
            )
            st.plotly_chart(fig_pie, use_container_width=True)

            st.write("#### ⏳ Cronología de ediciones")
            if not df_completo["year"].dropna().empty:
                fig_hist = px.histogram(
                    df_completo, x="year", nbins=25, labels={"year": "Año de publicación"},
                    color_discrete_sequence=["#2F5233"],
                )
                st.plotly_chart(fig_hist, use_container_width=True)

        # ---------- B) ANÁLISIS POR CDU ----------
        with subtab_cdu:
            st.subheader("🗂️ Concentración y rendimiento por secciones")

            df_metrics = df_completo.groupby("categoria").agg(
                Volumenes=("record_id", "count"), Prestados=("prestado", "sum"), Anio_Medio=("year", "mean"),
            ).reset_index()
            df_metrics["% Uso (Rotación)"] = (df_metrics["Prestados"] / df_metrics["Volumenes"] * 100).round(1)
            df_metrics["Año Medio Edición"] = df_metrics["Anio_Medio"].fillna(0).astype(int)
            df_metrics["es_infantil"] = df_metrics["categoria"].apply(_es_categoria_infantil)

            df_adultos = df_metrics[~df_metrics["es_infantil"]].sort_values(by="Volumenes", ascending=False)
            df_infantil = df_metrics[df_metrics["es_infantil"]].sort_values(by="Volumenes", ascending=False)

            st.markdown("### 👨‍💼 Análisis sección adultos")
            if not df_adultos.empty:
                fig_bar_adultos = px.bar(
                    df_adultos, x="categoria", y="Volumenes", color="% Uso (Rotación)",
                    title="Adultos: volumen vs rotación por categoría", color_continuous_scale="Greens",
                    labels={"categoria": "Categoría / CDU", "Volumenes": "Nº volúmenes"},
                )
                st.plotly_chart(fig_bar_adultos, use_container_width=True)
                tabla_ad = df_adultos[["categoria", "Volumenes", "% Uso (Rotación)", "Año Medio Edición"]]
                st.dataframe(tabla_ad, use_container_width=True, hide_index=True)
                st.download_button(
                    "📥 Descargar CSV (Adultos)", tabla_ad.to_csv(index=False, sep=";", encoding="utf-8-sig"),
                    "analisis_cdu_adultos.csv", "text/csv", key="csv_cdu_adultos",
                )
            else:
                st.info("Sin datos suficientes para la sección de adultos.")

            st.markdown("### 👶 Análisis sección infantil / juvenil")
            if not df_infantil.empty:
                fig_bar_infantil = px.bar(
                    df_infantil, x="categoria", y="Volumenes", color="% Uso (Rotación)",
                    title="Infantil/Juvenil: volumen vs rotación por categoría", color_continuous_scale="Oranges",
                    labels={"categoria": "Categoría / Tejuelo", "Volumenes": "Nº volúmenes"},
                )
                st.plotly_chart(fig_bar_infantil, use_container_width=True)
                tabla_inf = df_infantil[["categoria", "Volumenes", "% Uso (Rotación)", "Año Medio Edición"]]
                st.dataframe(tabla_inf, use_container_width=True, hide_index=True)
                st.download_button(
                    "📥 Descargar CSV (Infantil)", tabla_inf.to_csv(index=False, sep=";", encoding="utf-8-sig"),
                    "analisis_cdu_infantil.csv", "text/csv", key="csv_cdu_infantil",
                )
            else:
                st.info("Sin datos suficientes para la sección infantil/juvenil.")

        # ---------- C) ANÁLISIS PROFUNDO POR SIGNATURA ----------
        with subtab_signatura:
            st.subheader("🔎 Análisis profundo por signatura")

            filtro_pub = st.radio(
                "1. Selecciona la sección:", ["📚 Todo el fondo", "👨‍💼 Solo adultos", "👶 Solo infantil / juvenil"],
                horizontal=True, key="sig_filtro_pub",
            )
            df_nivel1 = df_completo.copy()
            if "adultos" in filtro_pub.lower():
                df_nivel1 = df_nivel1[~df_nivel1["es_infantil"]]
            elif "infantil" in filtro_pub.lower():
                df_nivel1 = df_nivel1[df_nivel1["es_infantil"]]

            st.markdown("---")
            st.markdown("#### 🎯 Criterios de selección y búsqueda")

            col_campo, col_busqueda, col_prestamos = st.columns([1, 2, 1])
            with col_campo:
                campo_busqueda_sig = st.selectbox(
                    "Buscar por:", ["signatura", "titulo", "autor", "materia"],
                    format_func=lambda x: {"signatura": "Signatura / CDU", "titulo": "Título", "autor": "Autor", "materia": "Materia"}[x],
                    key="sig_campo",
                )
            with col_busqueda:
                placeholder = (
                    "Ej: *(460.16)* para Navarra, 821* para literatura" if campo_busqueda_sig == "signatura"
                    else "Escribe el texto a buscar · admite comodines (*)"
                )
                busqueda_sig = st.text_input(f"⌨️ Buscar ({campo_busqueda_sig}):", value="", placeholder=placeholder, key="sig_busqueda")
            with col_prestamos:
                filtro_pr = st.selectbox(
                    "🪪 Historial préstamos:", ["Todos", "Nunca prestado (0)", "Préstamo estándar (1)", "Alta demanda (2)"],
                    key="sig_prestamo",
                )

            col_cat1, col_cat2 = st.columns(2)
            with col_cat1:
                opciones_cat = ["Todas"] + sorted(df_nivel1["categoria"].dropna().unique().tolist())
                filtro_cat = st.selectbox("🗂️ Categoría principal:", opciones_cat, key="sig_categoria")

            df_nivel2 = df_nivel1.copy()
            if filtro_cat != "Todas":
                df_nivel2 = df_nivel2[df_nivel2["categoria"] == filtro_cat]

            with col_cat2:
                raices_existentes = df_nivel2["signatura_real"].dropna().apply(_extraer_raiz).unique()
                opciones_sub = ["Todas"] + sorted(raices_existentes.tolist())
                filtro_sub = st.selectbox("🔎 Sub-signatura de la categoría:", opciones_sub, key="sig_sub")

            st.markdown("---")

            df_final_expurgo = df_nivel1.copy()
            if busqueda_sig.strip():
                columna = CAMPOS_BUSQUEDA_SIGNATURA.get(campo_busqueda_sig, "signatura_real")
                if columna == "signatura_real":
                    df_final_expurgo = df_final_expurgo[_wildcard_mask(df_final_expurgo[columna], busqueda_sig.strip().upper())]
                else:
                    df_final_expurgo = df_final_expurgo[_texto_contains_mask(df_final_expurgo[columna], busqueda_sig)]

            if filtro_cat != "Todas":
                df_final_expurgo = df_final_expurgo[df_final_expurgo["categoria"] == filtro_cat]
            if filtro_sub != "Todas":
                df_final_expurgo = df_final_expurgo[df_final_expurgo["signatura_real"].str.upper().str.startswith(filtro_sub, na=False)]

            if "Nunca" in filtro_pr:
                df_final_expurgo = df_final_expurgo[df_final_expurgo["prestamos"] == 0]
            elif "estándar" in filtro_pr.lower():
                df_final_expurgo = df_final_expurgo[df_final_expurgo["prestamos"] == 1]
            elif "Alta" in filtro_pr:
                df_final_expurgo = df_final_expurgo[df_final_expurgo["prestamos"] == 2]

            total_filtrado = len(df_final_expurgo)
            st.markdown(f"**Resultados encontrados: {total_filtrado} documentos**")

            # --- Indicadores de la selección (no es un filtro, solo informa) ---
            if total_filtrado:
                libros_prestados = (df_final_expurgo["prestamos"] > 0).sum()
                pct_pr = round((libros_prestados / total_filtrado) * 100, 1)
                anios_validos = df_final_expurgo["year"].dropna()
                anio_medio_col = int(anios_validos.mean()) if not anios_validos.empty else "Sin datos"
                st.markdown("##### Σ Resumen de la selección")
                r1, r2, r3 = st.columns(3)
                r1.metric("Nº volúmenes", f"{total_filtrado:,}")
                r2.metric("% préstamos", f"{pct_pr}%")
                r3.metric("Año medio", anio_medio_col)
            else:
                st.info("ℹ️ Modifica los criterios de búsqueda para calcular los indicadores del fondo.")

            # --- CSV del conjunto filtrado completo ---
            tabla_completa = df_final_expurgo[["record_id", "signatura_real", "titulo", "year", "categoria", "prestamos"]].copy()
            tabla_completa.columns = ["id_sistema", "Signatura", "Título", "Año", "Categoría", "Préstamos"]
            st.download_button(
                "📥 Descargar CSV (todos los resultados)",
                tabla_completa.to_csv(index=False, sep=";", encoding="utf-8-sig"),
                "analisis_signatura.csv", "text/csv", key="csv_sig",
            )

            # --- Paginación para no cargar tablas enormes en memoria de golpe (clave con 512MB) ---
            page_size = st.selectbox("Filas por página:", [50, 100, 250, 500], index=1, key="sig_page_size")
            total_paginas = max(1, (total_filtrado - 1) // page_size + 1) if total_filtrado else 1
            pagina = st.number_input("Página:", min_value=1, max_value=total_paginas, value=1, step=1, key="sig_page")
            inicio = (pagina - 1) * page_size
            tabla_pagina = tabla_completa.iloc[inicio: inicio + page_size]
            st.caption(f"Mostrando {inicio + 1}–{min(inicio + page_size, total_filtrado)} de {total_filtrado}")

            tabla_con_ficha(tabla_pagina, key="tabla_sig", id_col="id_sistema", origen="sesion")

    # ==========================================
    # BLOQUE 2: RECOMENDACIONES DE COMPRA
    # ==========================================
    with pestana_compras:
        subtab_rec_gen, subtab_rec_cdu = st.tabs(["🌐 A) Recomendaciones Generales", "📚 B) Recomendaciones por CDU"])

        # ---------- A) RECOMENDACIONES GENERALES ----------
        with subtab_rec_gen:
            st.subheader("📈 Títulos más populares en la red ausentes en tu centro")
            limite_gen = st.number_input("Número de títulos a sugerir:", min_value=5, max_value=200, value=50, step=5, key="lim_gen")

            if conn is not None:
                df_rec_gen = obtener_recomendaciones_automaticas(conn, biblioteca_seleccionada, limite_gen)
                if not df_rec_gen.empty:
                    df_rec_gen_mostrar = df_rec_gen.rename(columns={
                        "id_sistema": "id_sistema", "titulo": "Título", "autor": "Autor",
                        "anio": "Año", "total_bibliotecas": "Nº Bibliotecas en Red",
                    })
                    tabla_con_ficha(df_rec_gen_mostrar, key="tabla_rec_gen", id_col="id_sistema", origen="red")
                    csv_gen = df_rec_gen_mostrar.to_csv(index=False, sep=";", encoding="utf-8-sig")
                    st.download_button("📥 Descargar listado general (CSV)", csv_gen, "sugerencias_generales.csv", "text/csv", key="csv_gen")
                else:
                    st.info("No se encontraron recomendaciones pendientes.")
            else:
                st.error("No hay conexión activa con la base de datos.")

        # ---------- B) RECOMENDACIONES POR CDU ----------
        with subtab_rec_cdu:
            st.subheader("🎯 Sugerencias de adquisición por CDU")

            if conn is None:
                st.error("No hay conexión activa con la base de datos.")
            else:
                col_f1, col_f2 = st.columns(2)
                with col_f1:
                    limite_cdu = st.number_input("Máximo por subcategoría:", min_value=1, max_value=100, value=10, key="l_cdu")
                with col_f2:
                    anio_minimo = st.number_input("Año mínimo publicación:", min_value=1800, max_value=2026, value=2015, key="a_cdu")

                col_campo_cdu, col_busq_cdu = st.columns([1, 2])
                with col_campo_cdu:
                    campo_busqueda_cdu = st.selectbox(
                        "Filtrar por:", ["cdu", "titulo", "autor", "materia"],
                        format_func=lambda x: {"cdu": "CDU", "titulo": "Título", "autor": "Autor", "materia": "Materia"}[x],
                        key="cdu_campo",
                    )
                with col_busq_cdu:
                    ph = "Ej: 004* para informática" if campo_busqueda_cdu == "cdu" else "Texto a buscar · admite comodines (*)"
                    busqueda_cdu = st.text_input(f"⌨️ Buscar ({campo_busqueda_cdu}):", value="", placeholder=ph, key="cdu_busqueda")

                biblioteca_norm = biblioteca_seleccionada.upper().strip()

                with st.spinner("Modelando el embudo de categorías de la red..."):
                    df_raw_cdu = cargar_base_recomendaciones_cdu(conn, biblioteca_norm, int(anio_minimo))

                if df_raw_cdu.empty:
                    st.warning("No hay recomendaciones con la configuración de años actual.")
                else:
                    busqueda_cdu = busqueda_cdu.strip()
                    if busqueda_cdu:
                        if campo_busqueda_cdu == "titulo":
                            df_raw_cdu = df_raw_cdu[_texto_contains_mask(df_raw_cdu["titulo"], busqueda_cdu)]
                        elif campo_busqueda_cdu == "autor":
                            df_raw_cdu = df_raw_cdu[_texto_contains_mask(df_raw_cdu["autor"], busqueda_cdu)]
                        elif campo_busqueda_cdu == "materia":
                            ids_ok = _ids_con_materia(conn, df_raw_cdu["id_sistema"].tolist(), busqueda_cdu)
                            df_raw_cdu = df_raw_cdu[df_raw_cdu["id_sistema"].isin(ids_ok)]
                        else:
                            df_raw_cdu = df_raw_cdu[_wildcard_mask(df_raw_cdu["cdu"], busqueda_cdu.upper())]

                    if df_raw_cdu.empty:
                        st.info("ℹ️ Ninguna sugerencia de la red coincide con el filtro introducido.")
                    else:
                        clasif = df_raw_cdu.apply(
                            lambda r: _clasificar_libro_cdu(r["cdu"], r.get("todas_signaturas", "")), axis=1
                        )
                        df_raw_cdu = df_raw_cdu.copy()
                        df_raw_cdu["subtab_destino"] = [c[0] for c in clasif]
                        df_raw_cdu["categoria_final"] = [c[1] for c in clasif]
                        df_raw_cdu = df_raw_cdu[df_raw_cdu["subtab_destino"].notna()].sort_values("id_red_bibliotecas", ascending=False)

                        sub_adultos, sub_infantil = st.tabs(["👨‍💼 Sección Adultos", "👶 Sección Infantil"])

                        with sub_adultos:
                            hay_ad = False
                            for k, titulo_ex in MENUS_ADULTOS.items():
                                g = df_raw_cdu[(df_raw_cdu["subtab_destino"] == "Adultos") & (df_raw_cdu["categoria_final"] == k)].head(limite_cdu)
                                if not g.empty:
                                    hay_ad = True
                                    with st.expander(f"{titulo_ex} ({len(g)} ítems)"):
                                        g_mostrar = g[["id_sistema", "titulo", "autor", "anio", "cdu", "id_red_bibliotecas"]].rename(
                                            columns={"titulo": "Título", "autor": "Autor", "anio": "Año",
                                                     "cdu": "CDU", "id_red_bibliotecas": "Nº bibliotecas"}
                                        )
                                        tabla_con_ficha(g_mostrar, key=f"tabla_cdu_ad_{k}", id_col="id_sistema", origen="red")
                                        st.download_button(
                                            f"📥 CSV — {titulo_ex}", g_mostrar.to_csv(index=False, sep=";", encoding="utf-8-sig"),
                                            f"recomendaciones_adultos_{k}.csv", "text/csv", key=f"csv_cdu_ad_{k}",
                                        )
                            if not hay_ad:
                                st.info("No hay sugerencias para adultos con este filtro.")

                        with sub_infantil:
                            hay_inf = False
                            for k, titulo_ex in MENUS_INFANTIL.items():
                                g = df_raw_cdu[(df_raw_cdu["subtab_destino"] == "Infantil") & (df_raw_cdu["categoria_final"] == k)].head(limite_cdu)
                                if not g.empty:
                                    hay_inf = True
                                    with st.expander(f"{titulo_ex} ({len(g)} ítems)"):
                                        g_mostrar = g[["id_sistema", "titulo", "autor", "anio", "cdu", "id_red_bibliotecas"]].rename(
                                            columns={"titulo": "Título", "autor": "Autor", "anio": "Año",
                                                     "cdu": "CDU", "id_red_bibliotecas": "Nº bibliotecas"}
                                        )
                                        tabla_con_ficha(g_mostrar, key=f"tabla_cdu_inf_{k}", id_col="id_sistema", origen="red")
                                        st.download_button(
                                            f"📥 CSV — {titulo_ex}", g_mostrar.to_csv(index=False, sep=";", encoding="utf-8-sig"),
                                            f"recomendaciones_infantil_{k}.csv", "text/csv", key=f"csv_cdu_inf_{k}",
                                        )
                            if not hay_inf:
                                st.info("No hay sugerencias para infantil con este filtro.")

else:
    st.markdown(
        '<p style="text-align:center; margin-top:3rem; color:var(--ink-soft);">'
        "Selecciona tu biblioteca y sube el listado topográfico y el catálogo para empezar el análisis."
        "</p>",
        unsafe_allow_html=True,
    )
