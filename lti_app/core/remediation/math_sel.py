"""Convert LaTeX math expressions to accessible HTML (Math SEL).

Math SEL (Structured Expression Language) converts LaTeX notation into
standard HTML using Unicode characters, <sup>, <sub>, and semantic markup.
"""

import re

from bs4 import BeautifulSoup, NavigableString, Tag


# Greek letter mapping: LaTeX command -> Unicode character
GREEK_LETTERS: dict[str, str] = {
    r"\alpha": "\u03B1", r"\beta": "\u03B2", r"\gamma": "\u03B3",
    r"\delta": "\u03B4", r"\epsilon": "\u03B5", r"\varepsilon": "\u03B5",
    r"\zeta": "\u03B6", r"\eta": "\u03B7", r"\theta": "\u03B8",
    r"\vartheta": "\u03D1", r"\iota": "\u03B9", r"\kappa": "\u03BA",
    r"\lambda": "\u03BB", r"\mu": "\u03BC", r"\nu": "\u03BD",
    r"\xi": "\u03BE", r"\pi": "\u03C0", r"\varpi": "\u03D6",
    r"\rho": "\u03C1", r"\varrho": "\u03F1", r"\sigma": "\u03C3",
    r"\varsigma": "\u03C2", r"\tau": "\u03C4", r"\upsilon": "\u03C5",
    r"\phi": "\u03C6", r"\varphi": "\u03D5", r"\chi": "\u03C7",
    r"\psi": "\u03C8", r"\omega": "\u03C9",
    # Uppercase
    r"\Alpha": "\u0391", r"\Beta": "\u0392", r"\Gamma": "\u0393",
    r"\Delta": "\u0394", r"\Epsilon": "\u0395", r"\Zeta": "\u0396",
    r"\Eta": "\u0397", r"\Theta": "\u0398", r"\Iota": "\u0399",
    r"\Kappa": "\u039A", r"\Lambda": "\u039B", r"\Mu": "\u039C",
    r"\Nu": "\u039D", r"\Xi": "\u039E", r"\Pi": "\u03A0",
    r"\Rho": "\u03A1", r"\Sigma": "\u03A3", r"\Tau": "\u03A4",
    r"\Upsilon": "\u03A5", r"\Phi": "\u03A6", r"\Chi": "\u03A7",
    r"\Psi": "\u03A8", r"\Omega": "\u03A9",
}

# Operator mapping: LaTeX command -> HTML entity or Unicode
OPERATORS: dict[str, str] = {
    r"\pm": "\u00B1",       # +/-
    r"\mp": "\u2213",
    r"\times": "\u00D7",    # x
    r"\div": "\u00F7",
    r"\cdot": "\u00B7",
    r"\ne": "\u2260",
    r"\neq": "\u2260",
    r"\le": "\u2264",
    r"\leq": "\u2264",
    r"\ge": "\u2265",
    r"\geq": "\u2265",
    r"\approx": "\u2248",
    r"\equiv": "\u2261",
    r"\sim": "\u223C",
    r"\propto": "\u221D",
    r"\infty": "\u221E",
    r"\partial": "\u2202",
    r"\nabla": "\u2207",
    r"\forall": "\u2200",
    r"\exists": "\u2203",
    r"\in": "\u2208",
    r"\notin": "\u2209",
    r"\subset": "\u2282",
    r"\supset": "\u2283",
    r"\subseteq": "\u2286",
    r"\supseteq": "\u2287",
    r"\cup": "\u222A",
    r"\cap": "\u2229",
    r"\emptyset": "\u2205",
    r"\sum": "\u2211",
    r"\prod": "\u220F",
    r"\int": "\u222B",
    r"\lim": "lim",
    r"\to": "\u2192",
    r"\rightarrow": "\u2192",
    r"\leftarrow": "\u2190",
    r"\Rightarrow": "\u21D2",
    r"\Leftarrow": "\u21D0",
    r"\leftrightarrow": "\u2194",
    r"\Leftrightarrow": "\u21D4",
    r"\therefore": "\u2234",
    r"\because": "\u2235",
    r"\ldots": "\u2026",
    r"\cdots": "\u22EF",
    r"\quad": "\u2003",     # em space
    r"\qquad": "\u2003\u2003",
}

# Delimiter mapping
DELIMITERS: dict[str, str] = {
    r"\{": "{",
    r"\}": "}",
    r"\langle": "\u27E8",
    r"\rangle": "\u27E9",
    r"\lfloor": "\u230A",
    r"\rfloor": "\u230B",
    r"\lceil": "\u2308",
    r"\rceil": "\u2309",
    r"\|": "\u2016",
}


class MathSELConverter:
    """Convert LaTeX math expressions to accessible HTML."""

    # Patterns for LaTeX delimiters
    _DISPLAY_DOLLAR = re.compile(r"\$\$(.+?)\$\$", re.DOTALL)
    _INLINE_DOLLAR = re.compile(r"(?<!\$)\$(?!\$)(.+?)(?<!\$)\$(?!\$)")
    _INLINE_PAREN = re.compile(r"\\\((.+?)\\\)")
    _DISPLAY_BRACKET = re.compile(r"\\\[(.+?)\\\]", re.DOTALL)

    def convert_latex_expression(self, expr: str) -> str:
        """Convert a single LaTeX expression to HTML.

        Args:
            expr: LaTeX expression (without delimiters).

        Returns:
            HTML string with Unicode and semantic markup.
        """
        result = expr.strip()

        # Remove \text{...} and \mathrm{...} wrappers
        result = re.sub(r"\\(?:text|mathrm|mathit|mathbf)\{([^}]*)\}", r"\1", result)

        # Convert fractions: \frac{a}{b} -> a/b
        result = re.sub(
            r"\\frac\{([^}]*)\}\{([^}]*)\}",
            lambda m: f"({self.convert_latex_expression(m.group(1))})/({self.convert_latex_expression(m.group(2))})",
            result,
        )

        # Convert square roots: \sqrt{x} -> sqrt x, \sqrt[n]{x} -> n-sqrt x
        result = re.sub(r"\\sqrt\[([^]]*)\]\{([^}]*)\}", lambda m: f"{m.group(1)}\u221A{m.group(2)}", result)
        result = re.sub(r"\\sqrt\{([^}]*)\}", lambda m: f"\u221A{m.group(1)}", result)

        # Convert superscripts: x^{2} -> x<sup>2</sup>, x^2 -> x<sup>2</sup>
        result = re.sub(
            r"\^\{([^}]*)\}",
            lambda m: f"<sup>{self.convert_latex_expression(m.group(1))}</sup>",
            result,
        )
        result = re.sub(
            r"\^([a-zA-Z0-9])",
            r"<sup>\1</sup>",
            result,
        )

        # Convert subscripts: x_{1} -> x<sub>1</sub>, x_1 -> x<sub>1</sub>
        result = re.sub(
            r"_\{([^}]*)\}",
            lambda m: f"<sub>{self.convert_latex_expression(m.group(1))}</sub>",
            result,
        )
        result = re.sub(
            r"_([a-zA-Z0-9])",
            r"<sub>\1</sub>",
            result,
        )

        # Convert overline/underline
        result = re.sub(r"\\overline\{([^}]*)\}", r'<span style="text-decoration: overline;">\1</span>', result)
        result = re.sub(r"\\underline\{([^}]*)\}", r"<u>\1</u>", result)

        # Replace Greek letters (longest match first)
        for cmd, char in sorted(GREEK_LETTERS.items(), key=lambda x: -len(x[0])):
            result = result.replace(cmd, char)

        # Replace operators
        for cmd, char in sorted(OPERATORS.items(), key=lambda x: -len(x[0])):
            result = result.replace(cmd, char)

        # Replace delimiters
        for cmd, char in sorted(DELIMITERS.items(), key=lambda x: -len(x[0])):
            result = result.replace(cmd, char)

        # Clean up remaining braces
        result = result.replace("{", "").replace("}", "")

        # Clean up multiple spaces
        result = re.sub(r"\s+", " ", result).strip()

        return result

    def convert_page_html(self, soup: BeautifulSoup) -> tuple[BeautifulSoup, int]:
        """Convert all LaTeX expressions in a page to accessible HTML.

        Args:
            soup: Parsed HTML document.

        Returns:
            Tuple of (modified soup, count of conversions made).
        """
        conversions = 0

        # Remove MathJax/KaTeX script tags
        for script in soup.find_all("script"):
            src = script.get("src", "")
            text = script.string or ""
            if re.search(r"mathjax|katex|math-renderer", src, re.IGNORECASE) or \
               re.search(r"mathjax|katex|math-renderer", text, re.IGNORECASE):
                script.decompose()
                conversions += 1

        # Convert <code> elements containing LaTeX
        for code in soup.find_all("code"):
            code_text = code.get_text()
            if re.search(r"\\(?:frac|sqrt|alpha|beta|gamma|sum|int|lim)", code_text):
                converted = self.convert_latex_expression(code_text)
                code.replace_with(BeautifulSoup(converted, "html.parser"))
                conversions += 1

        # Process text nodes for LaTeX delimiters
        conversions += self._convert_text_nodes(soup)

        return soup, conversions

    def _convert_text_nodes(self, soup: BeautifulSoup) -> int:
        """Walk the DOM and convert LaTeX in text nodes."""
        conversions = 0

        # Process all text nodes
        for text_node in list(soup.find_all(string=True)):
            if not isinstance(text_node, NavigableString):
                continue

            # Skip text inside <script>, <style>, <pre>
            parent = text_node.parent
            if parent and parent.name in ("script", "style", "pre"):
                continue

            original = str(text_node)
            converted = original

            # Replace display math first ($$...$$), then \[...\]
            for pattern in (self._DISPLAY_DOLLAR, self._DISPLAY_BRACKET):
                converted = pattern.sub(
                    lambda m: f'<span role="math" aria-label="{m.group(1).strip()}">{self.convert_latex_expression(m.group(1))}</span>',
                    converted,
                )

            # Replace inline math ($...$), then \(...\)
            for pattern in (self._INLINE_DOLLAR, self._INLINE_PAREN):
                converted = pattern.sub(
                    lambda m: f'<span role="math" aria-label="{m.group(1).strip()}">{self.convert_latex_expression(m.group(1))}</span>',
                    converted,
                )

            if converted != original:
                new_content = BeautifulSoup(converted, "html.parser")
                text_node.replace_with(new_content)
                conversions += 1

        return conversions

    def validate_no_remaining_latex(self, soup: BeautifulSoup) -> list[str]:
        """Check for any remaining unconverted LaTeX after conversion.

        Args:
            soup: Post-conversion HTML document.

        Returns:
            List of remaining LaTeX fragments found.
        """
        remaining = []
        full_text = soup.get_text()

        # Check for remaining dollar-sign delimiters
        for match in re.finditer(r"\$[^$]+\$", full_text):
            fragment = match.group()
            # Skip currency amounts like $10.00
            if re.match(r"^\$\d+(\.\d{2})?$", fragment):
                continue
            remaining.append(fragment[:50])

        # Check for remaining LaTeX commands
        for match in re.finditer(r"\\[a-zA-Z]+", full_text):
            cmd = match.group()
            if cmd not in (r"\n", r"\t", r"\r"):
                remaining.append(cmd)

        return remaining
