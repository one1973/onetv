# -*- coding: utf-8 -*-
import sys
import re
import os
import tempfile
import json
import subprocess
import urllib.parse
import requests
from bs4 import BeautifulSoup
import xbmc
import xbmcgui
import xbmcplugin

# =====================================================================
# CONFIGURACIÓN
# =====================================================================
YT_DLP_PATH = r"C:\yt-dlp\yt-dlp.exe"
BASE_URL = "https://reypelis.net"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

HANDLE = int(sys.argv[1])
ADDON_URL = sys.argv[0]

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "es-ES,es;q=0.9"})

# =====================================================================
# UTILIDADES
# =====================================================================
def build_url(params):
    return f"{ADDON_URL}?{urllib.parse.urlencode(params)}"

def get_soup(url):
    try:
        r = SESSION.get(url, timeout=15)
        r.raise_for_status()
        return BeautifulSoup(r.text, "html.parser")
    except Exception as e:
        xbmcgui.Dialog().notification("Reypelis", f"Error: {e}", xbmcgui.NOTIFICATION_ERROR, 5000)
        return None

def unpack_js(packed_js):
    pattern = r"eval\(function\(p,a,c,k,e,d\).*?\}\('(.*?)',(\d+),(\d+),'(.*?)'\.split\('\|'\)"
    match = re.search(pattern, packed_js, re.DOTALL)
    if not match:
        return None
    payload, base, count, symtab_raw = match.groups()
    base = int(base)
    symtab = symtab_raw.split('|')
    def decode_char(c):
        if c.isdigit(): return int(c)
        elif c.isalpha(): return ord(c) - 87 if c.islower() else ord(c) - 29
        else: return 0
    def decode_word(word):
        result = []
        i = 0
        while i < len(word):
            c = word[i]
            if c.isalnum():
                num_str = c
                i += 1
                while i < len(word) and word[i].isalnum():
                    num_str += word[i]
                    i += 1
                index = 0
                for char in num_str:
                    index = index * base + decode_char(char)
                if index < len(symtab) and symtab[index]:
                    result.append(symtab[index])
                else:
                    result.append(num_str)
            else:
                result.append(c)
                i += 1
        return ''.join(result)
    return re.sub(r'\b\w+\b', lambda m: decode_word(m.group(0)), payload)

# =====================================================================
# MENÚS Y LISTADOS
# =====================================================================
def main_menu():
    items = [
        ("Películas", {"action": "list", "url": f"{BASE_URL}/cartelera-peliculas/"}),
        ("Series",    {"action": "list", "url": f"{BASE_URL}/cartelera-series/"}),
        ("Buscar",    {"action": "search"}),
    ]
    for label, params in items:
        li = xbmcgui.ListItem(label=label)
        xbmcplugin.addDirectoryItem(HANDLE, build_url(params), li, isFolder=True)
    xbmcplugin.endOfDirectory(HANDLE)

def search():
    kb = xbmc.Keyboard("", "Buscar en Reypelis")
    kb.doModal()
    if kb.isConfirmed() and kb.getText():
        url = f"{BASE_URL}/?s={urllib.parse.quote_plus(kb.getText())}"
        list_content(url)

def list_content(url):
    soup = get_soup(url)
    if not soup: return
    for article in soup.select("article.post"):
        title_tag = article.select_one("h2.entry-title")
        link_tag  = article.select_one("a.lnk-blk")
        img_tag   = article.select_one(".post-thumbnail img")
        if not (title_tag and link_tag): continue
        title = title_tag.get_text(strip=True)
        movie_url = link_tag["href"]
        thumb = img_tag["src"] if img_tag else ""
        if thumb.startswith("//"): thumb = "https:" + thumb
        li = xbmcgui.ListItem(label=title)
        li.setArt({"thumb": thumb, "icon": thumb})
        li.setInfo("video", {"title": title})
        params = {"action": "detail", "url": movie_url, "title": title}
        xbmcplugin.addDirectoryItem(HANDLE, build_url(params), li, isFolder=True)
    xbmcplugin.endOfDirectory(HANDLE)

def detail(url, title):
    soup = get_soup(url)
    if not soup: return
    plot = ""
    desc = soup.select_one("div.description p")
    if desc: plot = desc.get_text(strip=True)
    year = ""
    y_tag = soup.select_one("span.year")
    if y_tag: year = y_tag.get_text(strip=True)
    options = []
    for div in soup.select("div.video.aa-tb"):
        iframe = div.select_one("iframe")
        if not iframe: continue
        src = iframe.get("data-src") or iframe.get("src")
        if not src: continue
        div_id = div.get("id", "")
        opt_link = soup.select_one(f'ul.aa-tbs-video a[href="#{div_id}"]')
        lang = "Opción"
        if opt_link:
            server_span = opt_link.select_one("span.server")
            if server_span:
                lang = server_span.get_text(strip=True).strip("-").strip()
        options.append({"url": src, "lang": lang})
    if not options:
        xbmcgui.Dialog().ok("Reypelis", "No se encontraron servidores.")
        return
    for opt in options:
        li = xbmcgui.ListItem(label=opt["lang"])
        li.setInfo("video", {"title": title, "plot": plot, "year": year})
        params = {"action": "play", "url": opt["url"], "title": title}
        xbmcplugin.addDirectoryItem(HANDLE, build_url(params), li, isFolder=False)
    xbmcplugin.endOfDirectory(HANDLE)

# =====================================================================
# REPRODUCCIÓN
# =====================================================================
def play(embed_url, title):
    xbmc.log(f"Reypelis: === INICIANDO PLAY ===", xbmc.LOGINFO)
    xbmc.log(f"Reypelis: Resolviendo {embed_url}", xbmc.LOGINFO)
    
    try:
        # 1. Obtener URL de Fastream
        r = SESSION.get(embed_url, timeout=15)
        r.raise_for_status()
        match_fastream = re.search(r'SRC="(https://fastream\.to/embed-[^"]+)"', r.text, re.IGNORECASE)
        if not match_fastream:
            soup = BeautifulSoup(r.text, 'html.parser')
            iframe = soup.find('iframe')
            if iframe and iframe.get('src'):
                fastream_url = iframe['src']
            else:
                xbmcgui.Dialog().ok("Reypelis", "No se encontró el reproductor.")
                xbmcplugin.setResolvedUrl(HANDLE, False, xbmcgui.ListItem())
                return
        else:
            fastream_url = match_fastream.group(1)
        xbmc.log(f"Reypelis: URL Fastream: {fastream_url}", xbmc.LOGINFO)

        # 2. Extraer URL del .m3u8 (scraping manual)
        headers_fastream = {
            "User-Agent": USER_AGENT,
            "Referer": "https://reypelis.net/",
            "Accept": "*/*"
        }
        r_fast = SESSION.get(fastream_url, headers=headers_fastream, timeout=15)
        r_fast.raise_for_status()
        html_fast = r_fast.text

        script_blocks = re.findall(r'<script[^>]*>(.*?)</script>', html_fast, re.DOTALL | re.IGNORECASE)
        unpacked_code = None
        for script in script_blocks:
            if 'eval(function(p,a,c,k,e,d)' in script:
                unpacked = unpack_js(script)
                if unpacked:
                    unpacked_code = unpacked
                    break
        search_target = unpacked_code if unpacked_code else html_fast
        match_file = re.search(r'file\s*:\s*["\'](https?://[^"\']+\.m3u8[^"\']*)["\']', search_target)
        if match_file:
            m3u8_url = match_file.group(1)
        else:
            match_m3u8 = re.search(r'(https?://[^\s"\'<>]+\.m3u8[^\s"\'<>]*)', search_target)
            if match_m3u8:
                m3u8_url = match_m3u8.group(1)
            else:
                xbmcgui.Dialog().ok("Reypelis", "No se pudo extraer el enlace .m3u8.")
                xbmcplugin.setResolvedUrl(HANDLE, False, xbmcgui.ListItem())
                return
        xbmc.log(f"Reypelis: URL m3u8: {m3u8_url}", xbmc.LOGINFO)

        # 3. Descargar el .m3u8 con cabeceras
        headers_m3u8 = {
            "User-Agent": USER_AGENT,
            "Referer": fastream_url,
            "Origin": "https://fastream.to",
            "Accept": "*/*"
        }
        xbmc.log("Reypelis: Descargando m3u8 con cabeceras...", xbmc.LOGINFO)
        r_m3u8 = SESSION.get(m3u8_url, headers=headers_m3u8, timeout=15)
        
        if r_m3u8.status_code == 403:
            xbmcgui.Dialog().ok("Reypelis", "El servidor rechazó la descarga (403).\nInténtalo más tarde.")
            xbmcplugin.setResolvedUrl(HANDLE, False, xbmcgui.ListItem())
            return
        
        r_m3u8.raise_for_status()
        m3u8_content = r_m3u8.text
        xbmc.log(f"Reypelis: m3u8 descargado ({len(m3u8_content)} bytes)", xbmc.LOGINFO)

        # 4. Reescribir rutas relativas a absolutas
        lines = m3u8_content.splitlines()
        new_lines = []
        base_url = m3u8_url.rsplit('/', 1)[0] + '/'
        for line in lines:
            line = line.strip()
            if not line:
                continue
            if line.startswith('#'):
                new_lines.append(line)
            else:
                if line.startswith('http'):
                    new_lines.append(line)
                elif line.startswith('/'):
                    new_lines.append(f"https://s40.fastream.to{line}")
                else:
                    new_lines.append(f"{base_url}{line}")

        # 5. Guardar en archivo temporal
        temp_dir = tempfile.gettempdir()
        local_m3u8 = os.path.join(temp_dir, "reypelis_stream.m3u8")
        with open(local_m3u8, "w", encoding="utf-8") as f:
            f.write("\n".join(new_lines))
        xbmc.log(f"Reypelis: Archivo local guardado en {local_m3u8}", xbmc.LOGINFO)

        # 6. Reproducir archivo local
        li = xbmcgui.ListItem(path=local_m3u8)
        li.setMimeType("application/vnd.apple.mpegurl")
        li.setContentLookup(False)
        li.setProperty("inputstream", "inputstream.adaptive")
        li.setProperty("inputstream.adaptive.manifest_type", "hls")
        headers_str = (
            f"Referer={urllib.parse.quote(fastream_url)}"
            f"&Origin={urllib.parse.quote('https://fastream.to')}"
            f"&User-Agent={urllib.parse.quote(USER_AGENT)}"
        )
        li.setProperty("inputstream.adaptive.stream_headers", headers_str)
        xbmcplugin.setResolvedUrl(HANDLE, True, li)
        xbmc.log("Reypelis: === FIN PLAY ===", xbmc.LOGINFO)

    except Exception as e:
        xbmc.log(f"Reypelis: ERROR en play(): {e}", xbmc.LOGERROR)
        xbmcgui.Dialog().ok("Reypelis", f"Error al reproducir:\n{str(e)}")
        xbmcplugin.setResolvedUrl(HANDLE, False, xbmcgui.ListItem())

# =====================================================================
# ROUTER
# =====================================================================
if __name__ == "__main__":
    params = dict(urllib.parse.parse_qsl(sys.argv[2][1:])) if len(sys.argv) > 2 else {}
    action = params.get("action", "")
    if action == "":
        main_menu()
    elif action == "list":
        list_content(params["url"])
    elif action == "search":
        search()
    elif action == "detail":
        detail(params["url"], params.get("title", ""))
    elif action == "play":
        play(params["url"], params.get("title", ""))
    else:
        main_menu()