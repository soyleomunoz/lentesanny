#!/usr/bin/env python3
"""
Generador de páginas estáticas del blog — estándar GestixPro.

Por qué existe: el blog de la landing se lee en un modal, pero Google y los
buscadores de IA no indexan bien un contenido que solo vive dentro de un modal,
y WhatsApp/Facebook no leen lo que va después del "#". Este script toma los
artículos tal como están en index.html (única fuente de verdad) y genera:

  blog/<slug>.html        página estática completa e indexable por artículo
  og/blog/<slug>.jpg      imagen para compartir (1200x630) de cada artículo
  og-image.jpg            imagen para compartir del sitio (si no existe o con --og-home)
  sitemap.xml             inicio + páginas internas + artículos
  robots.txt              permite buscadores y bots de IA
  llms.txt                resumen del sitio para buscadores de IA

En la landing, cada tarjeta enlaza a blog/<slug>.html y un script intercepta el
clic para abrir el modal: el visitante sigue viendo el modal y los buscadores
encuentran la página.

Uso:  python3 tools/blog_estatico.py            (lee tools/blog.config.json)
      python3 tools/blog_estatico.py --og-home  (regenera también og-image.jpg)

Tras agregar o cambiar un artículo en index.html hay que ejecutar el script y
subir los archivos generados.
"""
import argparse
import datetime as dt
import html
import json
import os
import re
import subprocess
import sys
import tempfile
import unicodedata

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
CFG = json.load(open(os.path.join(ROOT, 'tools', 'blog.config.json'), encoding='utf-8'))
SITE = CFG['site'].rstrip('/')
E = html.escape

MESES = ['enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio', 'julio', 'agosto',
         'septiembre', 'octubre', 'noviembre', 'diciembre']


def slugify(t):
    t = unicodedata.normalize('NFKD', t).encode('ascii', 'ignore').decode().lower()
    return re.sub(r'[^a-z0-9]+', '-', t).strip('-')[:70].strip('-')


def fecha_larga(iso):
    d = dt.date.fromisoformat(iso)
    return f'{d.day} de {MESES[d.month - 1]} de {d.year}'


# ── Lectura de datos JS de index.html ─────────────────────────────────────────
NODE_EXTRAER = r"""
const fs = require('fs'), vm = require('vm');
const [file, nombre] = process.argv.slice(2);
const SRC = fs.readFileSync(file, 'utf8');
const m = new RegExp('(?:const|let|var)\\s+' + nombre + '\\s*=\\s*').exec(SRC);
if (!m) { console.error('No se encontró ' + nombre); process.exit(2); }
const ini = m.index + m[0].length;
let prof = 0, str = null, esc = false; const pl = [];
for (let j = ini; j < SRC.length; j++) {
  const c = SRC[j];
  if (str) {
    if (esc) { esc = false; continue; }
    if (c === '\\') { esc = true; continue; }
    if (str === '`' && c === '$' && SRC[j + 1] === '{') { pl.push(prof); prof++; j++; str = null; continue; }
    if (c === str) str = null;
    continue;
  }
  if (c === "'" || c === '"' || c === '`') { str = c; continue; }
  if (c === '/' && SRC[j + 1] === '/') { j = SRC.indexOf('\n', j); continue; }
  if (c === '/' && SRC[j + 1] === '*') { j = SRC.indexOf('*/', j) + 1; continue; }
  if ('{[('.includes(c)) prof++;
  if ('}])'.includes(c)) {
    prof--;
    if (c === '}' && pl.length && prof === pl[pl.length - 1]) { pl.pop(); str = '`'; continue; }
    if (prof === 0) {
      const lit = SRC.slice(ini, j + 1);
      const val = vm.runInNewContext('(' + lit + ')', { Array, Object, String, Number, Math, JSON });
      process.stdout.write(JSON.stringify(val));
      process.exit(0);
    }
  }
}
console.error('Literal sin cerrar'); process.exit(3);
"""


def leer_literal_js(nombre, archivo='index.html'):
    with tempfile.NamedTemporaryFile('w', suffix='.js', delete=False) as f:
        f.write(NODE_EXTRAER)
    try:
        r = subprocess.run(['node', f.name, os.path.join(ROOT, archivo), nombre],
                           capture_output=True, text=True)
        if r.returncode:
            sys.exit(f'No se pudo leer {nombre} de {archivo}: {r.stderr.strip()}')
        out = r.stdout
    finally:
        os.unlink(f.name)
    return json.loads(out)


def cuerpo_a_html(body, formato, escapar=False):
    """Convierte el cuerpo del artículo al HTML de la página estática."""
    if escapar:  # el sitio inserta el texto con textContent: hay que escaparlo
        if formato == 'parrafos':
            body = [E(x) for x in body]
        elif formato == 'tuplas':
            body = [[b[0], [E(x) for x in b[1]] if isinstance(b[1], list) else E(b[1])] for b in body]
    if formato == 'html':
        return body.strip()
    if formato == 'parrafos':  # ['texto', 'texto', ...]  (admite HTML inline)
        return '\n'.join(f'<p>{p}</p>' for p in body)
    if formato == 'tuplas':  # [['p','texto'], ['h3','título'], ['ul',[...]]]
        out = []
        for item in body:
            tag, val = item[0], item[1]
            if tag in ('ul', 'ol'):
                out.append(f'<{tag}>' + ''.join(f'<li>{x}</li>' for x in val) + f'</{tag}>')
            else:
                tag = 'h4' if tag in ('h2', 'h3', 'h4') else tag
                out.append(f'<{tag}>{val}</{tag}>')
        return '\n'.join(out)
    raise ValueError('Formato de cuerpo desconocido: ' + formato)


def articulos_dom(f):
    """Artículos escritos como modales en el HTML (<div class="..." data-slug=... data-fecha=...>)."""
    from bs4 import BeautifulSoup
    sopa = BeautifulSoup(open(os.path.join(ROOT, f.get('archivo', 'index.html')), encoding='utf-8').read(), 'html.parser')
    res = []
    for m in sopa.select(f['modal']):
        titulo = m.select_one(f['titulo']).get_text(' ', strip=True)
        cat = m.select_one(f['categoria']).get_text(' ', strip=True) if f.get('categoria') and m.select_one(f['categoria']) else 'Blog'
        cuerpo = m.select_one(f['cuerpo'])
        cuerpo = BeautifulSoup(str(cuerpo), 'html.parser').select_one(f['cuerpo'])
        for sel in f.get('excluir', []):
            for x in cuerpo.select(sel):
                x.decompose()
        res.append({'key': m.get('id'), 'slug': m['data-slug'], 'titulo': titulo,
                    'resumen': m.get('data-resumen', ''), 'categoria': cat, 'fecha': m['data-fecha'],
                    'lectura': m.get('data-lectura'), 'portada': m.get('data-portada'),
                    'portada_alt': m.get('data-portada-alt') or titulo, 'html': cuerpo.decode_contents().strip()})
    return res


def articulos():
    """Normaliza los artículos según tools/blog.config.json → fuente."""
    f = CFG['fuente']
    if f.get('tipo') == 'dom':
        res = articulos_dom(f)
        res.sort(key=lambda x: x['fecha'], reverse=True)
        return res
    data = leer_literal_js(f['literal'])
    if 'ruta' in f:  # p. ej. "blog.posts" dentro de un objeto de traducciones
        for k in f['ruta'].split('.'):
            data = data[k]
    items = list(data.items()) if isinstance(data, dict) else list(enumerate(data))
    m = f['campos']
    res = []
    for key, a in items:
        g = lambda k, d=None: a.get(m.get(k, k), d) if m.get(k, k) else d
        titulo = g('titulo')
        slug = g('slug') or slugify(titulo)
        art = {
            'key': key, 'slug': slug, 'titulo': titulo,
            'resumen': g('resumen') or '', 'categoria': g('categoria') or f.get('categoria_defecto', 'Blog'),
            'fecha': g('fecha'), 'lectura': g('lectura'),
            'portada': g('portada') or (f['portada_patron'].format(slug=slug) if f.get('portada_patron') else None),
            'portada_alt': g('portada_alt') or titulo,
            'html': cuerpo_a_html(g('cuerpo'), f.get('formato', 'html'), f.get('escapar_texto', False)),
        }
        if not art['lectura']:
            palabras = len(re.sub('<[^>]+>', ' ', art['html']).split())
            art['lectura'] = max(2, round(palabras / 200))
        if not art['fecha']:
            sys.exit(f'Falta la fecha ISO (AAAA-MM-DD) del artículo "{titulo}"')
        if not art['resumen']:
            sys.exit(f'Falta el resumen del artículo "{titulo}"')
        res.append(art)
    res.sort(key=lambda x: x['fecha'], reverse=True)
    return res


# ── Página estática ───────────────────────────────────────────────────────────
def con_ids(cuerpo):
    """Agrega id a cada subtítulo (h3/h4) y arma el índice del artículo."""
    toc = []
    def rep(m):
        txt = re.sub('<[^>]+>', '', m.group(2)).strip()
        i = slugify(txt)[:40] or f's{len(toc)}'
        toc.append((i, txt))
        return f'<h4{m.group(1)} id="{i}">{m.group(2)}</h4>'
    cuerpo = re.sub(r'<h[34]((?:(?!\bid=)[^>])*)>(.*?)</h[34]>', rep, cuerpo, flags=re.S)
    return cuerpo, toc


def pagina(a, todos):
    t = CFG['tema']
    url = f'{SITE}/blog/{a["slug"]}.html'
    og = f'{SITE}/og/blog/{a["slug"]}.jpg'
    cuerpo, toc = con_ids(a['html'])
    # Rutas relativas de la landing → relativas a /blog/
    cuerpo = re.sub(r'(src|href)="(?!https?:|mailto:|tel:|#|/|data:|\.\./)([^"]+)"', r'\1="../\2"', cuerpo)
    lectura = f' · Lectura de {a["lectura"]} min' if a.get('lectura') else ''
    autor = CFG.get('autor', {'@type': 'Organization', 'name': CFG['nombre']})
    ld_post = {
        '@context': 'https://schema.org', '@type': 'BlogPosting', 'headline': a['titulo'],
        'description': a['resumen'], 'image': og, 'datePublished': a['fecha'],
        'dateModified': a['fecha'], 'inLanguage': CFG.get('lang', 'es-CL'),
        'mainEntityOfPage': url, 'author': autor,
        'publisher': {'@type': 'Organization', 'name': CFG['nombre'], 'url': SITE + '/'},
    }
    ld_bc = {'@context': 'https://schema.org', '@type': 'BreadcrumbList', 'itemListElement': [
        {'@type': 'ListItem', 'position': 1, 'name': 'Inicio', 'item': SITE + '/'},
        {'@type': 'ListItem', 'position': 2, 'name': 'Blog', 'item': SITE + '/' + CFG.get('ancla_blog', '#blog')},
        {'@type': 'ListItem', 'position': 3, 'name': a['titulo'], 'item': url}]}
    nav = ''.join(f'<a href="../{h}">{E(n)}</a>' for n, h in CFG.get('nav', []))
    cta = CFG['cta']
    otros = [x for x in todos if x['slug'] != a['slug']][:4]
    mas = ''.join(f'<li><a href="{x["slug"]}.html">{E(x["titulo"])}</a></li>' for x in otros)
    toc_html = ''
    if len(toc) >= 3:
        toc_html = ('<nav class="toc" aria-label="Contenido del artículo"><strong>En este artículo</strong><ol>'
                    + ''.join(f'<li><a href="#{i}">{E(x)}</a></li>' for i, x in toc) + '</ol></nav>')
    portada = ''
    if a.get('portada'):
        portada = (f'<figure class="cover"><img src="../{a["portada"]}" alt="{E(a["portada_alt"])}" '
                   f'width="1200" height="630" fetchpriority="high"></figure>')
    logo = CFG['logo_html'].replace('src="', 'src="../') if 'src="' in CFG['logo_html'] else CFG['logo_html']
    creditos = CFG.get('creditos_html', 'Desarrollado por <a href="https://gestixpro.cl/" target="_blank" rel="noopener">GestixPro</a>')
    fuentes = CFG.get('fuentes_html', '').replace('url(assets/', 'url(../assets/')
    fav = CFG.get('favicon', 'favicon.ico')
    fav = fav if fav.startswith(('data:', 'http')) else '../' + fav
    nota = f'<p class="note">{CFG["nota_articulo"]}</p>' if CFG.get('nota_articulo') else ''
    return f'''<!DOCTYPE html>
<html lang="{CFG.get('lang', 'es-CL')}">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<!-- Página generada por tools/blog_estatico.py a partir de index.html: no editar a mano. -->
<title>{E(a['titulo'])} | {E(CFG['nombre'])}</title>
<meta name="description" content="{E(a['resumen'])}">
<link rel="canonical" href="{url}">
<meta name="robots" content="index, follow, max-image-preview:large">
<meta name="theme-color" content="{t['fondo']}">
<meta property="og:type" content="article">
<meta property="og:locale" content="{CFG.get('lang', 'es-CL').replace('-', '_')}">
<meta property="og:site_name" content="{E(CFG['nombre'])}">
<meta property="og:title" content="{E(a['titulo'])}">
<meta property="og:description" content="{E(a['resumen'])}">
<meta property="og:url" content="{url}">
<meta property="og:image" content="{og}">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta property="og:image:alt" content="{E(a['titulo'])}">
<meta property="article:published_time" content="{a['fecha']}">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="{E(a['titulo'])}">
<meta name="twitter:description" content="{E(a['resumen'])}">
<meta name="twitter:image" content="{og}">
<link rel="icon" href="{fav}">
<script type="application/ld+json">{json.dumps(ld_post, ensure_ascii=False)}</script>
<script type="application/ld+json">{json.dumps(ld_bc, ensure_ascii=False)}</script>
{fuentes}
<style>
:root{{--bg:{t['fondo']};--bg2:{t['fondo2']};--tx:{t['texto']};--mu:{t['suave']};--ac:{t['acento']};--ac2:{t.get('acento2', t['acento'])};--bd:{t['borde']};--hd:{t.get('titulos', t['texto'])};--btn-tx:{t.get('boton_texto', '#fff')};--hbg:{t.get('cabecera', 'color-mix(in srgb,' + t['fondo'] + ' 92%,transparent)')};--htx:{t.get('cabecera_texto', t['texto'])}}}
*{{box-sizing:border-box}}html,body{{overflow-x:hidden}}
body{{margin:0;background:var(--bg);color:var(--tx);font-family:{t['fuente_texto']};font-size:17px;line-height:1.75;-webkit-font-smoothing:antialiased}}
img{{max-width:100%;height:auto;display:block}}a{{color:var(--ac)}}:focus-visible{{outline:3px solid var(--ac);outline-offset:3px;border-radius:6px}}
.skip{{position:absolute;left:-9999px}}.skip:focus{{left:12px;top:8px;background:var(--ac);color:var(--btn-tx);padding:10px 14px;border-radius:10px;z-index:9}}
header{{position:sticky;top:0;z-index:5;background:var(--hbg);-webkit-backdrop-filter:blur(12px);backdrop-filter:blur(12px);border-bottom:1px solid var(--bd)}}
.hd{{max-width:1160px;margin:0 auto;padding:12px 20px;display:flex;align-items:center;justify-content:space-between;gap:14px}}
.logo{{font-family:{t['fuente_titulos']};font-weight:800;font-size:1.35rem;color:var(--htx);text-decoration:none;display:flex;align-items:center}}.logo img{{height:40px;width:auto}}
.hd nav{{display:flex;gap:2px;flex-wrap:wrap;justify-content:flex-end;align-items:center}}
.hd nav a{{text-decoration:none;color:var(--htx);font-weight:600;font-size:.9rem;padding:8px 11px;border-radius:999px;white-space:nowrap}}.hd nav a:hover{{background:color-mix(in srgb,var(--htx) 12%,transparent)}}
.hd nav a.cta{{background:var(--ac);color:var(--btn-tx)}}
@media(max-width:860px){{.hd nav a:not(.cta){{display:none}}}}
.hd nav a .sm{{display:none}}@media(max-width:560px){{.hd nav a .lg{{display:none}}.hd nav a .sm{{display:inline}}.logo{{font-size:1.15rem}}.logo img{{height:34px}}}}
main{{max-width:780px;margin:0 auto;padding:28px 20px 48px}}
.crumbs{{font-size:.86rem;color:var(--mu);margin-bottom:18px}}.crumbs a{{color:var(--mu)}}
.cat{{display:inline-block;font-size:.74rem;font-weight:700;letter-spacing:.1em;text-transform:uppercase;color:var(--ac);background:var(--bg2);border:1px solid var(--bd);padding:5px 12px;border-radius:999px}}
h1{{font-family:{t['fuente_titulos']};font-size:clamp({t.get('h1_min', '1.8rem')},4vw,{t.get('h1_max', '2.6rem')});line-height:1.18;letter-spacing:-.01em;color:var(--hd);margin:14px 0 10px;font-weight:800}}
.dek{{font-size:1.12rem;color:var(--mu);margin:0}}.meta{{font-size:.88rem;color:var(--mu);margin:12px 0 22px}}
.cover{{margin:0 0 8px}}.cover img{{border-radius:18px;width:100%;aspect-ratio:1200/630;object-fit:cover;background:var(--bg2)}}
.toc{{background:var(--bg2);border:1px solid var(--bd);border-radius:16px;padding:14px 22px;margin:24px 0}}.toc strong{{display:block;color:var(--hd)}}.toc ol{{margin:4px 0 0;padding-left:20px}}.toc a{{text-decoration:none}}
article h4{{font-family:{t['fuente_titulos']};font-size:1.4rem;line-height:1.3;color:var(--hd);margin:34px 0 10px}}
article p{{margin:0 0 16px}}article ul,article ol{{margin:0 0 18px;padding-left:22px}}article li{{margin-bottom:8px}}article strong{{color:var(--hd)}}
.note{{background:var(--bg2);border:1px solid var(--bd);border-radius:14px;padding:14px 18px;font-size:.92rem;color:var(--mu)}}
.box{{margin:36px 0 10px;border-radius:24px;padding:28px 24px;background:linear-gradient(135deg,var(--ac),var(--ac2));color:var(--btn-tx)}}.box h2{{font-family:{t['fuente_titulos']};margin:0 0 6px;font-size:1.45rem;color:inherit}}.box p{{margin:0 0 16px;opacity:.9}}
.btn{{display:inline-block;background:var(--btn-tx);color:var(--ac);font-weight:700;text-decoration:none;border-radius:999px;padding:13px 22px}}
.more{{margin-top:32px}}.more h2{{font-family:{t['fuente_titulos']};font-size:1.2rem;color:var(--hd)}}.more ul{{padding-left:20px}}.more li{{margin-bottom:6px}}
.back{{display:inline-block;margin-top:18px;font-weight:600}}
footer{{border-top:1px solid var(--bd);color:var(--mu);font-size:.88rem;padding:26px 20px 40px}}footer .in{{max-width:1160px;margin:0 auto;display:flex;flex-wrap:wrap;justify-content:space-between;gap:10px}}footer a{{color:var(--ac);font-weight:600}}
{CFG.get('css_extra', '')}
</style>
</head>
<body>
<a class="skip" href="#contenido">Saltar al contenido</a>
<header><div class="hd">
  <a class="logo" href="../" aria-label="{E(CFG['nombre'])}, ir al inicio">{logo}</a>
  <nav aria-label="Navegación principal">{nav}<a class="cta" href="{cta['url']}" target="_blank" rel="noopener"><span class="lg">{E(cta['boton'])}</span><span class="sm">{E(cta.get('boton_corto', 'Contacto'))}</span></a></nav>
</div></header>
<main id="contenido">
  <nav class="crumbs" aria-label="Ruta de navegación"><a href="../">Inicio</a> / <a href="../{CFG.get('ancla_blog', '#blog')}">Blog</a> / {E(a['categoria'])}</nav>
  <article>
    <span class="cat">{E(a['categoria'])}</span>
    <h1>{E(a['titulo'])}</h1>
    <p class="dek">{E(a['resumen'])}</p>
    <p class="meta">{E(CFG.get('firma', CFG['nombre']))} · <time datetime="{a['fecha']}">{fecha_larga(a['fecha'])}</time>{lectura}</p>
    {portada}
    {toc_html}
{cuerpo}
    {nota}
  </article>
  <section class="box" aria-label="{E(cta['titulo'])}"><h2>{E(cta['titulo'])}</h2><p>{E(cta['texto'])}</p><a class="btn" href="{cta['url']}" target="_blank" rel="noopener">{E(cta['boton'])}</a></section>
  {'<section class="more" aria-label="Más artículos"><h2>Más artículos</h2><ul>' + mas + '</ul></section>' if mas else ''}
  <a class="back" href="../{CFG.get('ancla_blog', '#blog')}">← Volver al blog</a>
</main>
<footer><div class="in">
  <span>© {dt.date.today().year} {E(CFG['nombre'])}. Todos los derechos reservados.</span>
  <span>{creditos}</span>
</div></footer>
</body>
</html>
'''


# ── Imágenes para compartir (1200x630) ────────────────────────────────────────
def html_og(img_rel, kicker, titulo, pie, estilo=None, posicion='center', fondo_css=None):
    t = CFG['tema']
    img = f'file://{os.path.join(ROOT, img_rel)}' if img_rel else ''
    fuentes = CFG.get('fuentes_html', '').replace('url(assets/', f'url(file://{ROOT}/assets/')
    fondo = (f'<img class="bg" src="{img}" style="object-position:{posicion}">' if img else '')
    if img and estilo == 'lateral':
        # Imagen recortada o de producto (PNG) a la derecha, texto a la izquierda
        fondo = f'<img class="lat" src="{img}">'
    if img and (estilo or CFG.get('og_estilo', 'portada')) == 'portada':
        # La imagen para compartir es la misma portada, recortada a 1200x630
        return f'<!DOCTYPE html><html><head><meta charset="utf-8"><style>*{{margin:0}}body{{width:1200px;height:630px;overflow:hidden;background:{t["fondo"]}}}.bg{{width:100%;height:100%;object-fit:cover;display:block}}</style></head><body>{fondo}</body></html>'
    tam = 64 if len(titulo) < 60 else 54 if len(titulo) < 95 else 46
    return f'''<!DOCTYPE html><html><head><meta charset="utf-8">{fuentes}<style>
*{{margin:0;box-sizing:border-box}}body{{width:1200px;height:630px;overflow:hidden;position:relative;
background:{fondo_css or 'linear-gradient(135deg,' + t['fondo'] + ',' + t['acento'] + ')'};font-family:{t['fuente_texto']}}}
.bg{{position:absolute;inset:0;width:100%;height:100%;object-fit:cover}}
.lat{{position:absolute;right:40px;top:40px;width:470px;height:550px;object-fit:contain;object-position:center bottom;filter:drop-shadow(0 20px 40px rgba(0,0,0,.35))}}
body.l .tx{{right:540px;bottom:auto;top:50%;transform:translateY(-50%)}}body.l .sh{{background:none}}
.sh{{position:absolute;inset:0;background:linear-gradient(180deg,rgba(0,0,0,.05) 20%,rgba(0,0,0,.55) 55%,rgba(0,0,0,.88))}}
.tx{{position:absolute;left:64px;right:64px;bottom:56px;color:#fff}}
.k{{display:inline-block;font-size:22px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;background:{t['acento']};color:{t.get('boton_texto', '#fff')};padding:8px 18px;border-radius:999px;margin-bottom:20px}}
h1{{font-family:{t['fuente_titulos']};font-size:{tam}px;line-height:1.12;font-weight:800;text-shadow:0 2px 18px rgba(0,0,0,.4);
display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden}}
.p{{margin-top:22px;font-size:24px;font-weight:600;opacity:.92}}
</style></head><body class="{'l' if img and estilo == 'lateral' else ''}">{fondo}<div class="sh"></div><div class="tx">
{f'<span class="k">{E(kicker)}</span>' if kicker else ''}<h1>{E(titulo)}</h1><p class="p">{E(pie)}</p></div></body></html>'''


def renderizar(trabajos):
    """trabajos: lista de (html, ruta_salida_jpg)."""
    if not trabajos:
        return
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception:
            b = p.chromium.launch(executable_path='/usr/bin/google-chrome')
        pg = b.new_page(viewport={'width': 1200, 'height': 630})
        for doc, salida in trabajos:
            os.makedirs(os.path.dirname(salida), exist_ok=True)
            tmp = os.path.join(ROOT, '.og-tmp.html')
            open(tmp, 'w', encoding='utf-8').write(doc)
            pg.goto('file://' + tmp)
            pg.wait_for_load_state('networkidle')
            pg.evaluate('document.fonts.ready')
            pg.screenshot(path=salida, type='jpeg', quality=86)
            os.unlink(tmp)
        b.close()


# ── sitemap, robots, llms ────────────────────────────────────────────────────
def sitemap(arts):
    hoy = dt.date.today().isoformat()
    urls = [(SITE + '/', hoy)]
    for u in CFG.get('paginas_extra', []):
        urls.append((SITE + '/' + u['ruta'], u.get('lastmod', hoy)))
    urls += [(f'{SITE}/blog/{a["slug"]}.html', a['fecha']) for a in arts]
    filas = '\n'.join(f'  <url><loc>{E(u)}</loc><lastmod>{m}</lastmod></url>' for u, m in urls)
    return f'<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n{filas}\n</urlset>\n'


ROBOTS = '''User-agent: *
Allow: /

# Buscadores y asistentes de IA
User-agent: GPTBot
Allow: /
User-agent: OAI-SearchBot
Allow: /
User-agent: ChatGPT-User
Allow: /
User-agent: ClaudeBot
Allow: /
User-agent: Claude-SearchBot
Allow: /
User-agent: Claude-User
Allow: /
User-agent: Google-Extended
Allow: /
User-agent: PerplexityBot
Allow: /
User-agent: Bingbot
Allow: /

Sitemap: {site}/sitemap.xml
'''


def llms(arts):
    L = CFG['llms']
    out = [f'# {CFG["nombre"]}', '', f'> {L["resumen"]}', '']
    out += [f'- {x}' for x in L.get('datos', [])]
    out += ['', '## Páginas principales', '']
    out += [f'- [{n}]({SITE}/{r}): {d}' for n, r, d in L.get('paginas', [])]
    if arts:
        out += ['', f'## {L.get("titulo_blog", "Blog")}', '']
        out += [f'- [{a["titulo"]}]({SITE}/blog/{a["slug"]}.html): {a["resumen"]}' for a in arts]
    return '\n'.join(out) + '\n'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--og-home', action='store_true', help='regenera og-image.jpg')
    ap.add_argument('--sin-og', action='store_true', help='no genera imágenes')
    args = ap.parse_args()
    arts = articulos() if CFG.get('fuente') else []
    os.makedirs(os.path.join(ROOT, 'blog'), exist_ok=True) if arts else None
    vigentes = set()
    for a in arts:
        ruta = os.path.join(ROOT, 'blog', a['slug'] + '.html')
        open(ruta, 'w', encoding='utf-8').write(pagina(a, arts))
        vigentes.add(a['slug'])
    # Elimina páginas generadas de artículos que ya no existen
    if os.path.isdir(os.path.join(ROOT, 'blog')):
        for f in os.listdir(os.path.join(ROOT, 'blog')):
            p = os.path.join(ROOT, 'blog', f)
            if f.endswith('.html') and f[:-5] not in vigentes and 'tools/blog_estatico.py' in open(p, encoding='utf-8').read(400):
                os.remove(p)
    trabajos = []
    if not args.sin_og:
        pie = CFG.get('og_pie', SITE.split('//')[1])
        ruta_h = os.path.join(ROOT, 'og', 'blog', 'huellas.json')
        huellas = json.load(open(ruta_h)) if os.path.exists(ruta_h) else {}
        for a in arts:
            salida = os.path.join(ROOT, 'og', 'blog', a['slug'] + '.jpg')
            firma = json.dumps([a.get('portada'), a['categoria'], a['titulo'], pie, CFG['tema']], ensure_ascii=False)
            if not os.path.exists(salida) or huellas.get(a['slug']) != firma:
                trabajos.append((html_og(a.get('portada'), a['categoria'], a['titulo'], pie), salida))
            huellas[a['slug']] = firma
        home = os.path.join(ROOT, 'og-image.jpg')
        oh = CFG.get('og_home')
        if oh and (args.og_home or not os.path.exists(home)):
            trabajos.append((html_og(oh.get('imagen'), oh.get('kicker', ''), oh['titulo'], pie, oh.get('estilo', 'titular'), oh.get('posicion', 'center'), oh.get('fondo')), home))
        renderizar(trabajos)
        if arts:
            huellas = {k: v for k, v in huellas.items() if k in vigentes}
            os.makedirs(os.path.dirname(ruta_h), exist_ok=True)
            json.dump(huellas, open(ruta_h, 'w'), ensure_ascii=False, indent=0)
    open(os.path.join(ROOT, 'sitemap.xml'), 'w').write(sitemap(arts))
    open(os.path.join(ROOT, 'robots.txt'), 'w').write(ROBOTS.format(site=SITE))
    open(os.path.join(ROOT, 'llms.txt'), 'w', encoding='utf-8').write(llms(arts))
    print(f'{len(arts)} artículos · {len(trabajos)} imágenes OG · sitemap, robots y llms actualizados')
    for a in arts:
        print(f'  blog/{a["slug"]}.html  ({a["fecha"]})')


if __name__ == '__main__':
    main()
