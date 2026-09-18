#!/usr/bin/env python3
"""Genererer static/icon-192.png ud fra kalender-mærket i static/favicon.svg.

    /usr/bin/python3 make_icons.py

Køres KUN når ikonet ændres — resultatet committes. Pillow er derfor et
build-værktøj, ikke en afhængighed: appen kører uden.

PNG'en er til to ting, som begge afviser SVG: manifestets 192-ikon og iOS'
apple-touch-icon. Manifestet har SVG'en ved siden af til alle andre.
"""
import os

from PIL import Image, ImageDraw

UD = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

BG = (249, 249, 247, 255)       # --bg, lys
BLAA = (42, 120, 214, 255)      # --accent
MOERK = (28, 92, 171, 255)      # topbjælken på kalenderen
RING = (22, 74, 138, 255)       # ringene
HVID = (255, 255, 255, 255)

SKALA = 4                       # tegn stort og skalér ned: PIL har ingen antialiasing


def tegn(px):
    s = px * SKALA
    img = Image.new("RGBA", (s, s), BG)
    d = ImageDraw.Draw(img)

    # 16 % margen: nok til at et maskable-ikon kan beskæres cirkulært, uden at
    # kalenderen rammer kanten.
    m = s * 0.16
    k = s - 2 * m                       # kalenderens bredde/højde
    r = k * 0.16

    # Selve kalenderen + den mørkere topbjælke
    d.rounded_rectangle([m, m + k * 0.10, m + k, m + k], radius=r, fill=BLAA)
    d.rounded_rectangle([m, m + k * 0.10, m + k, m + k * 0.30], radius=r, fill=MOERK)
    d.rectangle([m, m + k * 0.22, m + k, m + k * 0.30], fill=MOERK)

    # De to ringe foroven
    br = k * 0.075
    for x in (m + k * 0.24, m + k * 0.70):
        d.rounded_rectangle([x, m, x + br, m + k * 0.20], radius=br / 2, fill=RING)

    # Datofelterne: to rækker, som i favicon.svg
    fb = k * 0.14                        # feltets bredde
    for (rx, antal) in ((0.42, 3), (0.64, 2)):
        for i in range(antal):
            x = m + k * (0.13 + i * 0.26)
            y = m + k * rx
            d.rounded_rectangle([x, y, x + fb, y + fb], radius=fb * 0.28, fill=HVID)

    return img.resize((px, px), Image.LANCZOS)


def main():
    sti = os.path.join(UD, "icon-192.png")
    # Paletteret PNG: et fladt firfarvet mærke behøver ikke truecolor, og
    # filen skal med i Docker-imaget.
    tegn(192).convert("RGB").quantize(colors=32, method=Image.MEDIANCUT).save(
        sti, optimize=True)
    print(f"  {os.path.basename(sti)}  {os.path.getsize(sti):,} b")


if __name__ == "__main__":
    main()
