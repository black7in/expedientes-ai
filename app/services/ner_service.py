import os
import re
from pathlib import Path

_TIPO_LABELS = {
    "PER": "PERSONA",
    "CI":  "CI",
    "NIT": "NIT",
    "TEL": "TEL",
    "DIR": "DIRECCION",
}

_BERT_LABEL_MAP = {
    "PER": "PER", "PERSONA": "PER",
    "CI":  "CI",
    "NIT": "NIT",
    "TEL": "TEL", "TELEFONO": "TEL",
    "DIR": "DIR", "DIRECCION": "DIR",
}

ROLES_PER = {"DEMANDANTE", "DEMANDADO", "ABOGADO", "JUEZ", "TESTIGO", "NOTARIO"}

_MODELO_PATH = Path(os.getenv("NER_MODEL_PATH", "modelo_ner"))

_PATRONES: dict[str, re.Pattern] = {
    "CI": re.compile(
        r"C(?:édula(?:\s+de\s+Identidad)?|\.?\s*I\.?)"
        r"(?:\s+N(?:ro?|°|úm(?:ero)?)\.?)?\s*[:\-]?\s*\d{6,8}"
        r"(?:\s+(?:Cbba?\.?|L\.?P\.?|S\.?C\.?|Or\.?|Pt\.?|Be\.?|Pan\.?|Tj\.?|Sb\.?))?",
        re.IGNORECASE,
    ),
    "NIT": re.compile(
        r"N\.?I\.?T\.?\s*[:\-]?\s*\d{9,12}",
        re.IGNORECASE,
    ),
    "TEL": re.compile(
        r"""(?:
            (?:\+591|591)[-\s]?(?:[2-4]\d{6,7}|[67]\d{7})
            |
            (?:Telf?(?:éfono)?|Cel(?:ular)?|Tel\.?|Fax)[\s.]*[:\-]?\s*
            (?:(?:\+591|591)[-\s]?)?(?:[2-4]\d{6,7}|[67]\d{7})
            |
            \b[67]\d{7}\b
        )""",
        re.IGNORECASE | re.VERBOSE,
    ),
    "DIR": re.compile(
        r"(?:Calle|Av(?:enida)?\.?|Psj\.?|Pasaje|Barrio|B°\.?|Zona|Urb(?:anización)?\.?|Plaza)\s+"
        r"[\w\sáéíóúñÁÉÍÓÚÑ\-]{3,40}"
        r"(?:N(?:ro?|°)\.?\s*\d+|entre\s+[\w\s]{3,30}|esquina\s+[\w\s]{3,30})?",
        re.IGNORECASE,
    ),
}

_CHUNK_SIZE    = 1500
_CHUNK_OVERLAP = 100


class NerService:
    _pipeline = None

    @classmethod
    def cargar_modelo(cls) -> None:
        from transformers import pipeline as hf_pipeline
        cls._pipeline = hf_pipeline(
            "ner",
            model=str(_MODELO_PATH),
            aggregation_strategy="simple",
            device=-1,
        )
        print(f"[NER] Modelo BERT cargado desde {_MODELO_PATH}")

    # ── API pública ───────────────────────────────────────────────────────────

    def extraer_entidades(self, texto: str) -> list[dict]:
        entidades_bert  = self._ner_bert(texto)
        entidades_regex = self._ner_regex(texto)
        merged = self._merge(entidades_bert, entidades_regex)
        return self._asignar_placeholders(merged)

    def anonimizar(self, texto: str, entidades: list[dict]) -> str:
        for ent in sorted(entidades, key=lambda e: e["inicio"], reverse=True):
            texto = texto[: ent["inicio"]] + ent["placeholder"] + texto[ent["fin"]:]
        return texto

    def regenerar_anonimizado(self, texto: str, entidades: list[dict]) -> str:
        entidades = self.recompute_placeholders(list(entidades))
        for ent in sorted(entidades, key=lambda e: e["inicio"], reverse=True):
            texto = texto[: ent["inicio"]] + ent["placeholder"] + texto[ent["fin"]:]
        return texto

    def recompute_placeholders(self, entidades: list[dict]) -> list[dict]:
        """Recalcula placeholders respetando roles asignados por el usuario."""
        contadores: dict[str, int] = {}
        for ent in entidades:
            tipo = ent["tipo"]
            rol  = ent.get("rol") if tipo == "PER" else None
            label = rol if (rol and rol in ROLES_PER) else _TIPO_LABELS[tipo]
            contadores[label] = contadores.get(label, 0) + 1
            n = contadores[label]
            ent["placeholder"] = f"[{label}]" if n == 1 else f"[{label}_{n}]"
        return entidades

    # ── BERT ──────────────────────────────────────────────────────────────────

    def _ner_bert(self, texto: str) -> list[dict]:
        result: list[dict] = []
        vistos: set[tuple[int, int]] = set()

        for chunk, offset in self._chunkar(texto):
            for ent in self._pipeline(chunk):
                tipo = _BERT_LABEL_MAP.get(ent["entity_group"].upper())
                if tipo is None:
                    continue
                inicio = ent["start"] + offset
                fin    = ent["end"]   + offset
                if (inicio, fin) in vistos:
                    continue
                vistos.add((inicio, fin))
                result.append({
                    "texto":  ent["word"],
                    "tipo":   tipo,
                    "inicio": inicio,
                    "fin":    fin,
                    "score":  round(float(ent["score"]), 4),
                    "fuente": "bert",
                })
        return result

    def _chunkar(self, texto: str) -> list[tuple[str, int]]:
        chunks: list[tuple[str, int]] = []
        start = 0
        while start < len(texto):
            end = min(start + _CHUNK_SIZE, len(texto))
            if end < len(texto):
                ultimo_espacio = texto.rfind(" ", start, end)
                if ultimo_espacio > start:
                    end = ultimo_espacio
            chunks.append((texto[start:end], start))
            if end >= len(texto):
                break
            start = end - _CHUNK_OVERLAP
        return chunks

    # ── Regex ─────────────────────────────────────────────────────────────────

    def _ner_regex(self, texto: str) -> list[dict]:
        result = []
        for tipo, patron in _PATRONES.items():
            for m in patron.finditer(texto):
                result.append({
                    "texto":  m.group(0),
                    "tipo":   tipo,
                    "inicio": m.start(),
                    "fin":    m.end(),
                    "score":  0.95,
                    "fuente": "regex",
                })
        return result

    # ── Merge ─────────────────────────────────────────────────────────────────

    def _merge(self, bert: list[dict], regex: list[dict]) -> list[dict]:
        return self._resolver_solapamientos(regex + bert)

    def _resolver_solapamientos(self, entidades: list[dict]) -> list[dict]:
        sorted_ents = sorted(
            entidades,
            key=lambda e: (e["inicio"], 0 if e["fuente"] == "regex" else 1),
        )
        result: list[dict] = []
        for ent in sorted_ents:
            if result and ent["inicio"] < result[-1]["fin"]:
                continue
            result.append(ent)
        return result

    def _asignar_placeholders(self, entidades: list[dict]) -> list[dict]:
        for i, ent in enumerate(entidades):
            ent["id"]         = f"e{i + 1}"
            ent["confirmado"] = False
            ent.setdefault("rol", None)
        return self.recompute_placeholders(entidades)


ner_service = NerService()
