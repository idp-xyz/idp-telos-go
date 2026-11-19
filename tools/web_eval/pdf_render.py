"""Render pages of a PDF to PNG, so that a person (or an agent that can look at images) can annotate E4-PDF documents
by reading the page as printed, without any text extractor. Used because the machine has no pdftoppm.

  uv run --no-project --with pypdfium2==4.30.0 --with pillow==11.1.0 python -I tools/web_eval/pdf_render.py \\
      DOC.pdf OUTDIR 1,5,9-11 [scale]

Writes OUTDIR/<doc>-p<N>.png (scale 1.6 by default: about 1000 pixels wide for A4). Not imported by the tests.
"""
import sys
from pathlib import Path


def pages_arg(spec: str, n: int) -> list:
    out = []
    for part in spec.split(","):
        a, _, b = part.partition("-")
        out += list(range(int(a), int(b or a) + 1))
    return [p for p in out if 1 <= p <= n]


def main() -> int:
    import pypdfium2
    doc_path, out_dir, spec = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
    scale = float(sys.argv[4]) if len(sys.argv) > 4 else 1.6
    out_dir.mkdir(parents=True, exist_ok=True)
    doc = pypdfium2.PdfDocument(str(doc_path))
    for p in pages_arg(spec, len(doc)):
        img = doc[p - 1].render(scale=scale).to_pil()
        dest = out_dir / f"{doc_path.stem}-p{p}.png"
        img.save(dest)
        print(dest)
    return 0


if __name__ == "__main__":
    sys.exit(main())
