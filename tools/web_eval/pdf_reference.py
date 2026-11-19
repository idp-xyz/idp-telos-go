"""Reference PDF readers for E4-PDF, wrapped in the score command protocol (PDF on stdin, text on stdout, or a
non-zero exit and a reason word on stderr). They are NOT candidates for the service: pypdf and PDFium (and poppler's
pdftotext when installed) are only there to show what is achievable and to check the annotators' typing.

  uv run --no-project --with pypdf==6.1.1 --with cryptography==44.0.2 python -I tools/web_eval/pdf_reference.py pypdf
  uv run --no-project --with pypdfium2==4.30.0 python -I tools/web_eval/pdf_reference.py pdfium
  python3 -I tools/web_eval/pdf_reference.py pdftotext

They read the first --max-pages pages (default 200, the service's limit) and nothing else is defended: no
decompression cap, no recursion limit, no timeout. How they fare on the adversarial files is the finding.
Library imports are inside the functions: nothing here is imported by the tests.
"""
import argparse
import io
import subprocess
import sys

MIN_CHARS = 20  # fewer non-space characters in the whole document: no text layer


def fail(reason: str, msg: str = "") -> int:
    print(f"{reason}: {msg}"[:200].replace("\n", " "), file=sys.stderr)
    return 1


def read_pypdf(data: bytes, max_pages: int) -> list:
    import pypdf
    reader = pypdf.PdfReader(io.BytesIO(data))
    if reader.is_encrypted and not reader.decrypt(""):
        raise PermissionError("password required")
    return [(p.extract_text() or "") for p in reader.pages[:max_pages]]


def read_pdfium(data: bytes, max_pages: int) -> list:
    import pypdfium2
    try:
        doc = pypdfium2.PdfDocument(data)
    except pypdfium2.PdfiumError as e:
        if "password" in str(e).lower():
            raise PermissionError("password required") from None
        raise
    return [doc[i].get_textpage().get_text_range() for i in range(min(len(doc), max_pages))]


def read_pdftotext(data: bytes, max_pages: int) -> list:
    done = subprocess.run(["pdftotext", "-l", str(max_pages), "-layout", "-", "-"], input=data, capture_output=True)
    if done.returncode == 1:
        raise ValueError(done.stderr.decode("utf-8", "replace"))
    if done.returncode == 3:
        raise PermissionError("password required")
    return done.stdout.decode("utf-8", "replace").split("\f")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("engine", choices=["pypdf", "pdfium", "pdftotext"])
    ap.add_argument("--max-pages", type=int, default=200)
    args = ap.parse_args()
    data = sys.stdin.buffer.read()
    if not data.strip():
        return fail("malformed", "empty input")
    try:
        pages = {"pypdf": read_pypdf, "pdfium": read_pdfium, "pdftotext": read_pdftotext}[args.engine](data, args.max_pages)
    except PermissionError as e:
        return fail("encrypted", str(e))
    except MemoryError:
        return fail("too_large", "out of memory")
    except RecursionError:
        return fail("malformed", "recursion too deep")
    except Exception as e:  # every library error: the file could not be read
        return fail("malformed", f"{type(e).__name__}: {e}")
    text = "\n\n".join(pages)
    if len("".join(text.split())) < MIN_CHARS:
        return fail("no_text_layer", f"{len(pages)} pages, almost no text")
    sys.stdout.buffer.write(text.encode("utf-8", "replace"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
