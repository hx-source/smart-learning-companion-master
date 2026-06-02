from __future__ import annotations

import argparse
import json
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET


NS = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}


def extract_docx(path: Path) -> str:
    parts: list[str] = []
    with zipfile.ZipFile(path) as zf:
        xml = zf.read("word/document.xml")
    root = ET.fromstring(xml)
    for para in root.findall(".//w:p", NS):
        texts = [node.text or "" for node in para.findall(".//w:t", NS)]
        line = "".join(texts).strip()
        if line:
            parts.append(line)
    return "\n".join(parts)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("paths", nargs="+")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    data = {}
    for raw in args.paths:
        path = Path(raw)
        if path.suffix.lower() != ".docx":
            data[str(path)] = {"error": "Only .docx extraction is supported by this helper."}
            continue
        data[str(path)] = {"text": extract_docx(path)}

    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        for path, item in data.items():
            print(f"## {path}")
            print(item.get("text") or item.get("error", ""))
            print()


if __name__ == "__main__":
    main()
