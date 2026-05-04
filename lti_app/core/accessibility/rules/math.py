"""Accessibility rule for LaTeX/math content detection (WCAG 1.3.1)."""

import re

from bs4 import BeautifulSoup

from lti_app.models import AccessibilityIssue, IssueCategory, IssueSeverity
from lti_app.core.accessibility.rules.base import AccessibilityRule


class LatexInContentRule(AccessibilityRule):
    """MATH001: Detect LaTeX math expressions that need conversion to accessible HTML."""

    rule_id = "MATH001"
    severity = IssueSeverity.WARNING
    category = IssueCategory.MATH
    wcag_criterion = "1.3.1"
    message_template = "Page contains LaTeX math expressions that are not accessible"
    can_auto_fix = True
    fix_description = "Convert LaTeX expressions to accessible HTML using Math SEL"

    # Inline LaTeX delimiters
    _INLINE_DOLLAR = re.compile(r"(?<!\$)\$(?!\$)(.+?)(?<!\$)\$(?!\$)")
    _DISPLAY_DOLLAR = re.compile(r"\$\$(.+?)\$\$", re.DOTALL)
    _INLINE_PAREN = re.compile(r"\\\((.+?)\\\)")
    _DISPLAY_BRACKET = re.compile(r"\\\[(.+?)\\\]", re.DOTALL)

    # Common LaTeX commands that indicate math content
    _LATEX_COMMANDS = re.compile(
        r"\\(?:frac|sqrt|sum|prod|int|lim|infty|partial|nabla|"
        r"alpha|beta|gamma|delta|epsilon|zeta|eta|theta|iota|kappa|lambda|"
        r"mu|nu|xi|pi|rho|sigma|tau|upsilon|phi|chi|psi|omega|"
        r"Alpha|Beta|Gamma|Delta|Epsilon|Zeta|Eta|Theta|Iota|Kappa|Lambda|"
        r"Mu|Nu|Xi|Pi|Rho|Sigma|Tau|Upsilon|Phi|Chi|Psi|Omega|"
        r"pm|mp|times|div|cdot|ne|le|ge|leq|geq|approx|equiv|sim|"
        r"forall|exists|in|notin|subset|supset|cup|cap|"
        r"leftarrow|rightarrow|Leftarrow|Rightarrow|"
        r"begin|end|text|mathrm|mathbf|mathit|overline|underline|hat|vec)"
        r"(?:\{|[^a-zA-Z]|$)"
    )

    # MathJax/KaTeX script detection
    _MATHJAX_SCRIPTS = re.compile(
        r"mathjax|katex|math-renderer",
        re.IGNORECASE,
    )

    def check(self, soup: BeautifulSoup, page_id: str) -> list[AccessibilityIssue]:
        """Detect LaTeX math expressions in the page."""
        occurrences = 0

        # Check all text content
        full_text = soup.get_text()
        occurrences += len(self._INLINE_DOLLAR.findall(full_text))
        occurrences += len(self._DISPLAY_DOLLAR.findall(full_text))
        occurrences += len(self._INLINE_PAREN.findall(full_text))
        occurrences += len(self._DISPLAY_BRACKET.findall(full_text))

        # Check for LaTeX commands in text
        if self._LATEX_COMMANDS.search(full_text):
            occurrences += len(self._LATEX_COMMANDS.findall(full_text))

        # Check <code> elements for math content
        for code in soup.find_all("code"):
            code_text = code.get_text(strip=True)
            if self._LATEX_COMMANDS.search(code_text):
                occurrences += 1

        # Check for MathJax/KaTeX scripts
        for script in soup.find_all("script"):
            src = script.get("src", "")
            text = script.string or ""
            if self._MATHJAX_SCRIPTS.search(src) or self._MATHJAX_SCRIPTS.search(text):
                occurrences += 1

        if occurrences == 0:
            return []

        return [
            self.create_issue(
                page_id=page_id,
                message=f"Found {occurrences} LaTeX math expression(s) that need conversion to accessible HTML",
                element_html=None,
            )
        ]
