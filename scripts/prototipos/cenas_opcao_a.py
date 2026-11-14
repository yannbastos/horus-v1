"""Protótipos estáticos originais; não importa nem altera o pipeline de produção.

Requer Pillow (sem instalação/download implícito). A fonte OFL é aberta diretamente
pelo FreeType/Pillow, somente neste processo; não é instalada no sistema.
  python scripts/prototipos/cenas_opcao_a.py
  python scripts/prototipos/cenas_opcao_a.py --output "C:/00. Projetos/01. HORUS/relatorios/PROTOTIPOS-CENAS-2026-11-14"

Gera seis cenas 1080×1920 (três modelos × dois conteúdos), seis cópias com guias,
uma visão geral e um manifesto de hashes/proveniência, todos fora do Git do Hórus.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re

from PIL import Image, ImageColor, ImageDraw, ImageFont, __version__ as PILLOW_VERSION

ROOT = Path(__file__).resolve().parents[2]
W, H = 1080, 1920
FONT = ROOT / 'frontend/public/fonts/ArchivoExpanded-SemiBold.ttf'
LICENSE = ROOT / 'frontend/public/fonts/OFL-Archivo.txt'
TOKENS = ROOT / 'frontend/src/styles/tokens.css'
CAPTION = (100, 1200, 960, 1600)  # reserva conservadora; não simula métricas exatas do libass
DIRECTIONS = ('esquerda', 'direita', 'cima', 'baixo')


def palette():
    block = TOKENS.read_text(encoding='utf-8').split('[data-theme="dark"] {', 1)[1].split('}', 1)[0]
    return dict(re.findall(r'--(\w+):\s*(#[0-9A-Fa-f]+)', block))


C = palette()


def rgba(token, opacity=255):
    value = ImageColor.getcolor(C[token], 'RGBA')
    if opacity == 255 and value[3] < 255:
        # ImageDraw em RGBA atribui alpha; a cena final RGB precisa da mistura
        # explícita para preservar a transparência dos tokens Line/LineStrong.
        background = ImageColor.getcolor(C['Background'], 'RGB')
        return (*[round(v * value[3] / 255 + b * (1 - value[3] / 255)) for v, b in zip(value[:3], background)], 255)
    return (*value[:3], round(value[3] * opacity / 255))


def font(size):
    return ImageFont.truetype(str(FONT), size)


def transformed(box, t, direction):
    # Mesma ordem CSS do KenBurns: centro + escala * (ponto - centro + pan).
    scale = 1 + .15 * t
    dx, dy = {'esquerda': (-.03 * t * W, 0), 'direita': (.03 * t * W, 0),
              'cima': (0, -.03 * t * H), 'baixo': (0, .03 * t * H)}[direction]
    x0, y0, x1, y1 = box
    return (W / 2 + scale * (x0 - W / 2 + dx), H / 2 + scale * (y0 - H / 2 + dy),
            W / 2 + scale * (x1 - W / 2 + dx), H / 2 + scale * (y1 - H / 2 + dy))


def source_crop(direction):
    dx, dy = {'esquerda': (-.03 * W, 0), 'direita': (.03 * W, 0),
              'cima': (0, -.03 * H), 'baixo': (0, .03 * H)}[direction]
    return (W / 2 - W / 2 / 1.15 - dx, H / 2 - H / 2 / 1.15 - dy,
            W / 2 + W / 2 / 1.15 - dx, H / 2 + H / 2 / 1.15 - dy)


def safe_frame():
    boxes = [source_crop(direction) for direction in DIRECTIONS]
    return (max(b[0] for b in boxes), max(b[1] for b in boxes),
            min(b[2] for b in boxes), min(b[3] for b in boxes))


class Canvas:
    def __init__(self, label, title, subtitle):
        self.image = Image.new('RGBA', (W, H), rgba('Background'))
        self.draw = ImageDraw.Draw(self.image)
        self.bounds = []
        # Elementos de fundo podem ser recortados: não carregam informação.
        self.draw.rectangle((0, 0, 24, H), fill=rgba('AccentInk'))
        self.draw.ellipse((650, -300, 1430, 480), outline=rgba('LineStrong'), width=2)
        self.draw.line((160, 225, 920, 225), fill=rgba('LineStrong'), width=2)
        # Bloco 380..1080: 69% da área segura 183..1200, acima das legendas.
        self.text(title, (150, 380, 930, 580), 72)
        self.text('PROTÓTIPO', (225, 1650, 855, 1695), 24, 'Muted', center=True)

    def text(self, value, box, size, color='Ink', center=False):
        x0, y0, x1, y1 = box
        for current in range(size, size - 1, -1):
            face = font(current)
            lines = []
            for paragraph in value.split('\n'):
                line = ''
                for word in paragraph.split():
                    trial = (line + ' ' + word).strip()
                    if self.draw.textlength(trial, font=face) > x1 - x0 and line:
                        lines.append(line)
                        line = word
                    else:
                        line = trial
                lines.append(line)
            advance = round(current * 1.35)
            widths = [self.draw.textlength(line, font=face) for line in lines]
            if max(widths, default=0) <= x1 - x0 and len(lines) * advance <= y1 - y0:
                break
        else:
            raise ValueError('Texto não cabe: ' + value)
        for i, line in enumerate(lines):
            x = (x0 + x1 - widths[i]) / 2 if center else x0
            y = y0 + i * advance
            self.draw.text((x, y), line, font=face, fill=rgba(color), anchor='lt')
            self.bounds.append(self.draw.textbbox((x, y), line, font=face, anchor='lt'))

    def panel(self, box):
        self.draw.rectangle(box, fill=rgba('Sunken'), outline=rgba('LineStrong'), width=2)
        self.bounds.append(box)

    def validate(self):
        # Todos os textos/formas informativas ficam visíveis em 101 posições das
        # quatro direções e fora da reserva de legenda (sobreposta após o zoom).
        for box in self.bounds:
            for direction in DIRECTIONS:
                for step in range(101):
                    left, top, right, bottom = transformed(box, step / 100, direction)
                    if not (0 <= left < right <= W and 0 <= top < bottom <= H):
                        raise ValueError(f'Recorte de conteúdo: {box}, {direction}, {step}')
                    if not (bottom <= CAPTION[1] or top >= CAPTION[3]):
                        raise ValueError(f'Conteúdo invade reserva de legenda: {box}, {direction}, {step}')


def pictogram(draw, kind, x, y, color):
    """Geometria autoral: nenhuma biblioteca de ícones/imagem externa."""
    if kind == 'observe':
        draw.ellipse((x, y, x + 56, y + 56), outline=color, width=5)
        draw.line((x + 47, y + 47, x + 73, y + 73), fill=color, width=7)
        draw.ellipse((x + 23, y + 23, x + 32, y + 32), fill=color)
    elif kind == 'test':
        draw.line((x + 23, y, x + 23, y + 29, x + 5, y + 65, x + 64, y + 65,
                   x + 46, y + 29, x + 46, y), fill=color, width=5)
        draw.line((x + 16, y + 48, x + 54, y + 48), fill=color, width=4)
    else:
        draw.ellipse((x, y, x + 68, y + 68), outline=color, width=4)
        draw.line((x + 15, y + 34, x + 29, y + 48, x + 54, y + 21), fill=color, width=6)


def flow(variant):
    if variant == 'a':
        title, subtitle = 'Da dúvida ao\naprendizado.', 'Um método simples para o cotidiano.'
        details = ('Olhar com atenção', 'Experimentar', 'Conferir mudanças')
    else:
        title, subtitle = 'Antes de\nescolher.', 'Aplicação: uma decisão de compra.'
        details = ('Entender o objetivo', 'Comparar opções', 'Rever a escolha')
    scene = Canvas('01 / FLUXO • ' + variant.upper(), title, subtitle)
    for index, (name, detail, kind) in enumerate(zip(('Observar', 'Testar', 'Revisar'), details, ('observe', 'test', 'review'))):
        y = 610 + index * 160
        scene.panel((150, y, 930, y + 145))
        icon = Image.new('RGBA', (90, 90))
        pictogram(ImageDraw.Draw(icon), kind, 5, 5, rgba('AccentInk'))
        scene.image.alpha_composite(icon.resize((140, 140), Image.Resampling.LANCZOS), (175, y + 3))
        scene.text(name, (345, y + 18, 905, y + 78), 44)
        scene.text(detail, (345, y + 87, 905, y + 133), 32, 'Muted')
        if index < 2:
            scene.draw.line((540, y + 146, 540, y + 154), fill=rgba('AccentInk'), width=3)
            scene.draw.polygon(((535, y + 152), (545, y + 152), (540, y + 159)), fill=rgba('AccentInk'))
    return scene


def comparison(variant):
    if variant == 'a':
        title, subtitle = 'Palpite ou\nevidência?', 'Comparação qualitativa • sem escala numérica.'
        names, rows = ('Sem\nregistro', 'Com\nregistro'), (('Palpite', 'Dúvida', 'Repetição'), ('Evidência', 'Contexto', 'Revisão'))
    else:
        title, subtitle = 'Uma ideia\npode evoluir.', 'Aplicação: revisão de um trabalho.'
        names, rows = ('Rascunho', 'Com\nrevisão'), (('Ideia inicial', 'Hipótese', 'Em aberto'), ('Contraste', 'Pergunta', 'Outro olhar'))
    scene = Canvas('02 / COMPARAÇÃO • ' + variant.upper(), title, subtitle)
    for side, x in enumerate((150, 550)):
        scene.panel((x, 610, x + 380, 1080))
        tone = 'Muted' if side == 0 else 'Good'
        scene.text(names[side], (x + 20, 635, x + 360, 755), 44, tone, center=True)
        # Dispersão × organização, sem barras que possam sugerir resultados medidos.
        points = [(x + 67, 810), (x + 155, 845), (x + 293, 789)] if side == 0 else [(x + 67, 816), (x + 190, 816), (x + 313, 816)]
        if side:
            scene.draw.line(points, fill=rgba(tone), width=3)
        for px, py in points:
            scene.draw.ellipse((px - 15, py - 15, px + 15, py + 15), fill=rgba(tone))
        for index, text in enumerate(rows[side]):
            scene.text(text, (x + 15, 898 + index * 56, x + 365, 944 + index * 56), 32, center=True)
    return scene


def concept(variant):
    title, subtitle, word, closing = (
        ('Perguntar\nmuda o olhar.', 'Conhecimento começa com curiosidade.', 'EVIDÊNCIA', 'Observar antes de concluir.') if variant == 'a' else
        ('Números pedem\ncontexto.', 'Interpretar também é relacionar.', 'CONTEXTO', 'Compare antes de decidir.'))
    scene = Canvas('03 / CONCEITO • ' + variant.upper(), title, subtitle)
    scene.panel((150, 610, 930, 1080))
    if variant == 'a':
        # Lente original ampliada em torno de um ponto: metáfora de observação.
        scene.draw.ellipse((420, 650, 640, 870), outline=rgba('AccentInk'), width=7)
        scene.draw.line((608, 838, 676, 906), fill=rgba('AccentInk'), width=11)
        scene.draw.ellipse((512, 742, 548, 778), fill=rgba('Good'))
    else:
        for index, offset in enumerate((0, 30, 60)):
            scene.draw.rectangle((405 + offset, 650 + offset, 615 + offset, 805 + offset),
                                 fill=rgba('Sunken'), outline=rgba('AccentInk'), width=4)
    scene.text(word, (180, 920, 900, 1018), 72, 'AccentInk', center=True)
    scene.text(closing, (175, 1024, 905, 1070), 32, center=True)
    return scene


def dashed(draw, box, color, width=3):
    left, top, right, bottom = map(round, box)
    for x in range(left, right, 22):
        draw.line((x, top, min(x + 12, right), top), fill=color, width=width)
        draw.line((x, bottom, min(x + 12, right), bottom), fill=color, width=width)
    for y in range(top, bottom, 22):
        draw.line((left, y, left, min(y + 12, bottom)), fill=color, width=width)
        draw.line((right, y, right, min(y + 12, bottom)), fill=color, width=width)


def guide(scene):
    image = scene.image.copy()
    layer = Image.new('RGBA', image.size)
    draw = ImageDraw.Draw(layer)
    frame = safe_frame()
    l, t, r, b = map(round, frame)
    for box in ((0, 0, W, t), (0, b, W, H), (0, t, l, b), (r, t, W, b)):
        draw.rectangle(box, fill=(0, 0, 0, 100))
    dashed(draw, frame, rgba('Warn'))
    draw.rectangle(CAPTION, fill=rgba('Sunken', 230), outline=rgba('Warn'), width=2)
    draw.text((125, 1224), 'RESERVA LIBASS • SIMULAÇÃO', font=font(25), fill=rgba('Warn'))
    for index, line in enumerate(('Observar antes', 'de decidir.')):
        draw.text((530, 1405 + index * 77), line, anchor='mt', font=font(58), fill=rgba('InkStrong'))
    draw.text((125, 80), 'CÓPIA DE REVISÃO • NÃO USAR COMO ASSET', font=font(23), fill=rgba('Ink'))
    draw.text((125, 137), 'MOLDURA COMUM: ZOOM 1,15× + PAN 3%', font=font(22), fill=rgba('Warn'))
    return Image.alpha_composite(image, layer)


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=Path('C:/00. Projetos/01. HORUS/relatorios/PROTOTIPOS-CENAS-2026-11-14'))
    args = parser.parse_args()
    output = args.output.resolve()
    if output.is_relative_to(ROOT):
        parser.error('As imagens devem ficar fora do repositório Hórus.')
    output.mkdir(parents=True, exist_ok=True)
    manifest = {'dimensions': [W, H], 'pillow_version': PILLOW_VERSION, 'tokens_sha256': sha(TOKENS), 'script_sha256': sha(Path(__file__)),
                'font': {'file': FONT.name, 'sha256': sha(FONT), 'license': 'SIL OFL 1.1', 'license_sha256': sha(LICENSE)},
                'ken_burns': {'maximum_scale': 1.15, 'pan_percent': 3, 'common_source_frame': safe_frame()},
                'caption_reservation': CAPTION, 'content': 'Geometria e textos originais, sem mídia de terceiros.',
                'validation': '101 posições × quatro direções por caixa informativa; sem recorte ou invasão da reserva.', 'files': []}
    thumbnails = []
    for name, create in (('01-fluxo', flow), ('02-comparacao', comparison), ('03-conceito', concept)):
        for variant in ('a', 'b'):
            scene = create(variant)
            scene.validate()
            for suffix, image in (('', scene.image), ('-guia', guide(scene))):
                destination = output / f'{name}-{variant}{suffix}.png'
                image.convert('RGB').save(destination, optimize=True)
                manifest['files'].append({'name': destination.name, 'sha256': sha(destination)})
            thumbnails.append(scene.image)
    overview = Image.new('RGB', (W, H), rgba('Background')[:3])
    draw = ImageDraw.Draw(overview)
    draw.text((54, 72), 'CENAS LOCAIS / OPÇÃO A', font=font(40), fill=rgba('Ink')[:3])
    draw.text((54, 145), 'Três modelos • dois conteúdos por modelo', font=font(24), fill=rgba('Muted')[:3])
    for index, image in enumerate(thumbnails):
        row, column = index % 2, index // 2
        overview.paste(image.convert('RGB').resize((306, 544), Image.Resampling.LANCZOS), (54 + column * 333, 260 + row * 650))
        draw.text((54 + column * 333, 816 + row * 650), f'{index // 2 + 1:02d} / {"AB"[index % 2]}', font=font(24), fill=rgba('AccentInk')[:3])
    draw.text((54, 1680), 'As cópias -guia mostram reserva de legenda', font=font(25), fill=rgba('Muted')[:3])
    draw.text((54, 1730), 'e moldura conservadora do zoom máximo.', font=font(25), fill=rgba('Muted')[:3])
    destination = output / '00-visao-geral.png'
    overview.save(destination, optimize=True)
    manifest['files'].append({'name': destination.name, 'sha256': sha(destination)})
    (output / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    (output / 'OFL-Archivo.txt').write_bytes(LICENSE.read_bytes())
    print(json.dumps({'output': str(output), 'png_count': len(manifest['files']), 'validated': True}, ensure_ascii=False))


if __name__ == '__main__':
    main()
