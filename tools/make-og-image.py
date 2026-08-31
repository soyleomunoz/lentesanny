# -*- coding: utf-8 -*-
"""Genera la imagen de previsualizacion social (Open Graph) 1200x630 de Anny."""
from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageEnhance
import random

W, H = 1200, 630
SAFE = 64                     # zona segura: nada relevante fuera de este margen

PURPLE      = (75, 46, 90)
PURPLE_DEEP = (34, 17, 43)
DARK        = (22, 22, 28)
TURQUOISE   = (47, 214, 208)

FONT = 'tools/Outfit.ttf'
def f(size, weight=b'Bold'):
    ft = ImageFont.truetype(FONT, size)
    ft.set_variation_by_name(weight)
    return ft

# ---------------------------------------------------------------- fondo
# Degradado diagonal violeta profundo -> casi negro, igual que el CTA final del sitio.
bg = Image.new('RGB', (W, H))
px = bg.load()
for y in range(H):
    for x in range(0, W, 4):
        t = min(1.0, max(0.0, x / W * 0.55 + y / H * 0.45))
        c = tuple(int(PURPLE_DEEP[i] + (DARK[i] - PURPLE_DEEP[i]) * t) for i in range(3))
        for dx in range(4):
            if x + dx < W:
                px[x + dx, y] = c

def glow(cx, cy, radius, color, strength, softness=0.55):
    """Halo radial suave, equivalente a los radial-gradient del sitio."""
    layer = Image.new('L', (W, H), 0)
    ImageDraw.Draw(layer).ellipse(
        [cx - radius, cy - radius, cx + radius, cy + radius], fill=int(255 * strength))
    layer = layer.filter(ImageFilter.GaussianBlur(radius * softness))
    bg.paste(Image.new('RGB', (W, H), color), (0, 0), layer)

glow( 120, 540, 400, PURPLE,    0.60)   # base violeta abajo a la izquierda
glow( 880, 330, 300, TURQUOISE, 0.20)   # contraluz detras del producto
glow(1080,  70, 240, TURQUOISE, 0.16)   # destello superior derecho

card = bg.convert('RGBA')

# ---------------------------------------------------------------- producto
prod = Image.open('assets/img/productos/lentes.png').convert('RGBA')
pw = 600
prod = prod.resize((pw, int(prod.height * pw / prod.width)), Image.LANCZOS)
prod = prod.rotate(-4, resample=Image.BICUBIC, expand=True)
# Los lentes son negros sobre fondo oscuro: se levantan un poco para que separen.
prod = ImageEnhance.Brightness(prod).enhance(1.22)
prod = ImageEnhance.Contrast(prod).enhance(1.10)

pxp, pyp = W - prod.width - 46, int(H / 2 - prod.height / 2) + 12

shadow = Image.new('RGBA', (W, H), (0, 0, 0, 0))
shadow.paste((0, 0, 0, 165), (pxp, pyp + 30), prod.split()[3])
card = Image.alpha_composite(card, shadow.filter(ImageFilter.GaussianBlur(30)))
card.alpha_composite(prod, (pxp, pyp))

d = ImageDraw.Draw(card)

# ---------------------------------------------------------------- marca
logo = Image.open('assets/img/anny_logotipo.png').convert('RGBA').resize((56, 56), Image.LANCZOS)
card.alpha_composite(logo, (SAFE, 58))

fb, fs = f(30, b'Bold'), f(19, b'Medium')
x = SAFE + 72
d.text((x, 72), 'Anny', font=fb, fill=(255, 255, 255))
x += d.textlength('Anny', font=fb) + 12
d.text((x, 81), 'by CaecusLab', font=fs, fill=(176, 174, 190))

# ---------------------------------------------------------------- titular
title = f(68, b'Bold')
d.text((SAFE, 186), 'Autonomía',  font=title, fill=(255, 255, 255))
d.text((SAFE, 262), 'al alcance', font=title, fill=TURQUOISE)

# regla corta, como el subrayado activo del menu
d.rounded_rectangle([SAFE, 360, SAFE + 66, 365], radius=3, fill=TURQUOISE)

sub = f(25, b'Regular')
for i, line in enumerate(['Lentes inteligentes con inteligencia',
                          'artificial para personas con',
                          'discapacidad visual.']):
    d.text((SAFE, 396 + i * 36), line, font=sub, fill=(203, 202, 214))

# ---------------------------------------------------------------- pie (dentro de la zona segura)
fu, fa = f(24, b'SemiBold'), f(19, b'Medium')
d.text((SAFE, 530), 'lentesanny.cl', font=fu, fill=TURQUOISE)
x = SAFE + d.textlength('lentesanny.cl', font=fu) + 18
d.ellipse([x, 543, x + 5, 548], fill=(120, 118, 132))
d.text((x + 17, 534), 'Con el respaldo de ANID', font=fa, fill=(150, 148, 162))

# ---------------------------------------------------------------- grano
# Ruido muy tenue para evitar bandeado del degradado al comprimir a JPG.
random.seed(7)
noise = Image.new('L', (W, H))
noise.putdata([random.randint(118, 138) for _ in range(W * H)])
card = Image.blend(card, Image.merge('RGBA', (noise, noise, noise, card.split()[3])), 0.03)

card.convert('RGB').save('assets/img/og-anny.jpg', quality=88, optimize=True, progressive=True)
print('generado assets/img/og-anny.jpg')
