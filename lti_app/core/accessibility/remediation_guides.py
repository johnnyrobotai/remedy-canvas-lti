"""WCAG remediation guidance per issue type.

Each guide provides human-readable steps for fixing common accessibility
issues found by the Remedy Canvas LTI accessibility analyzer.
"""

REMEDIATION_GUIDES: dict[str, dict] = {
    # -----------------------------------------------------------------------
    # Image rules (WCAG 1.1.1)
    # -----------------------------------------------------------------------
    "IMG001": {
        "title": "Missing Alt Text",
        "wcag": "1.1.1",
        "severity": "error",
        "category": "images",
        "description": "Images must have descriptive alternative text so screen reader users can understand the content.",
        "steps": [
            "Select the image in the Canvas Rich Content Editor.",
            "Click the image options (or right-click and choose 'Image Options').",
            "Enter descriptive alt text that conveys the image's purpose.",
            "If the image is purely decorative, check the 'Decorative Image' option (sets alt=\"\").",
            "Save the page.",
        ],
        "auto_fixable": True,
        "resources": [
            "https://www.w3.org/WAI/WCAG22/Understanding/non-text-content",
            "https://www.w3.org/WAI/tutorials/images/decision-tree/",
        ],
    },
    "IMG002": {
        "title": "Inadequate Alt Text",
        "wcag": "1.1.1",
        "severity": "warning",
        "category": "images",
        "description": "Alt text should meaningfully describe the image. Generic text like 'image' or filenames are not sufficient.",
        "steps": [
            "Review the current alt text for the image.",
            "Replace generic text (e.g., 'image.jpg', 'photo') with a concise description.",
            "Describe what the image conveys, not what it looks like.",
            "Keep alt text under 125 characters when possible.",
        ],
        "auto_fixable": True,
        "resources": [
            "https://www.w3.org/WAI/WCAG22/Understanding/non-text-content",
        ],
    },
    "IMG003": {
        "title": "Duplicate Alt Text Across Images",
        "wcag": "1.1.1",
        "severity": "warning",
        "category": "images",
        "description": "Multiple images share the same alt text. Each image should have unique, descriptive alt text.",
        "steps": [
            "Identify images with identical alt text.",
            "Provide unique descriptions that distinguish each image.",
            "Consider what specific information each image conveys.",
        ],
        "auto_fixable": True,
        "resources": [
            "https://www.w3.org/WAI/WCAG22/Understanding/non-text-content",
        ],
    },
    "IMG004": {
        "title": "Excessively Long Alt Text",
        "wcag": "1.1.1",
        "severity": "warning",
        "category": "images",
        "description": "Alt text is very long. Consider using a shorter alt and providing extended description elsewhere.",
        "steps": [
            "Shorten the alt text to a brief summary (under 125 characters).",
            "For complex images (charts, diagrams), add a detailed text description below the image.",
            "Use aria-describedby to link to a longer description if needed.",
        ],
        "auto_fixable": True,
        "resources": [
            "https://www.w3.org/WAI/WCAG22/Understanding/non-text-content",
        ],
    },
    "IMG005": {
        "title": "Linked Image Missing Alt Text",
        "wcag": "1.1.1",
        "severity": "error",
        "category": "images",
        "description": "An image inside a link has no alt text. The alt text should describe the link destination.",
        "steps": [
            "Add alt text to the image that describes where the link goes.",
            "If the link also has visible text, the image alt can be empty (decorative).",
            "Never leave linked images without alt text.",
        ],
        "auto_fixable": True,
        "resources": [
            "https://www.w3.org/WAI/WCAG22/Understanding/non-text-content",
            "https://www.w3.org/WAI/tutorials/images/functional/",
        ],
    },
    # -----------------------------------------------------------------------
    # Heading rules (WCAG 1.3.1)
    # -----------------------------------------------------------------------
    "HDG001": {
        "title": "H1 Used in Course Content",
        "wcag": "1.3.1",
        "severity": "error",
        "category": "headings",
        "description": "Canvas reserves H1 for the page title. Content headings should start at H2.",
        "steps": [
            "Change the H1 heading to H2.",
            "Adjust any sub-headings accordingly (H2->H3, etc.).",
            "In the Rich Content Editor, select the text and change the heading level in the toolbar.",
        ],
        "auto_fixable": True,
        "resources": [
            "https://www.w3.org/WAI/WCAG22/Understanding/info-and-relationships",
        ],
    },
    "HDG002": {
        "title": "Skipped Heading Level",
        "wcag": "1.3.1",
        "severity": "warning",
        "category": "headings",
        "description": "Heading levels should not be skipped (e.g., H2 followed by H4). This breaks the document outline.",
        "steps": [
            "Review the heading hierarchy on the page.",
            "Ensure headings follow a logical order: H2, H3, H4, etc.",
            "Do not skip levels (e.g., going from H2 directly to H4).",
            "Restructure content if needed to maintain proper nesting.",
        ],
        "auto_fixable": True,
        "resources": [
            "https://www.w3.org/WAI/WCAG22/Understanding/info-and-relationships",
            "https://www.w3.org/WAI/tutorials/page-structure/headings/",
        ],
    },
    "HDG003": {
        "title": "Empty Heading",
        "wcag": "1.3.1",
        "severity": "error",
        "category": "headings",
        "description": "A heading element contains no text. Empty headings confuse screen reader users navigating by headings.",
        "steps": [
            "Add meaningful text to the heading.",
            "If the heading is not needed, remove the heading element entirely.",
            "Do not use headings for spacing; use CSS margin/padding instead.",
        ],
        "auto_fixable": True,
        "resources": [
            "https://www.w3.org/WAI/WCAG22/Understanding/info-and-relationships",
        ],
    },
    "HDG004": {
        "title": "Fake Heading (Bold/Large Text Instead of Heading)",
        "wcag": "1.3.1",
        "severity": "warning",
        "category": "headings",
        "description": "Text appears to be styled as a heading (bold, large) but is not marked up with a heading element.",
        "steps": [
            "Select the bold/large text.",
            "Change it to a proper heading level (H2, H3, etc.) using the toolbar.",
            "Remove manual bold/size styling that was simulating a heading.",
        ],
        "auto_fixable": True,
        "resources": [
            "https://www.w3.org/WAI/WCAG22/Understanding/info-and-relationships",
        ],
    },
    "HDG005": {
        "title": "No Heading Structure",
        "wcag": "1.3.1",
        "severity": "warning",
        "category": "headings",
        "description": "The page has substantial content but no headings. Headings help users navigate and understand content structure.",
        "steps": [
            "Identify logical sections in the content.",
            "Add appropriate headings (H2, H3) to introduce each section.",
            "Use headings to create a scannable outline of the content.",
        ],
        "auto_fixable": True,
        "resources": [
            "https://www.w3.org/WAI/WCAG22/Understanding/info-and-relationships",
        ],
    },
    "HDG006": {
        "title": "Excessively Long Heading",
        "wcag": "1.3.1",
        "severity": "warning",
        "category": "headings",
        "description": "The heading is very long. Headings should be concise labels for content sections.",
        "steps": [
            "Shorten the heading to a brief, descriptive label.",
            "Move detailed content into the paragraph below the heading.",
            "Aim for headings under 80 characters.",
        ],
        "auto_fixable": True,
        "resources": [
            "https://www.w3.org/WAI/WCAG22/Understanding/info-and-relationships",
        ],
    },
    # -----------------------------------------------------------------------
    # Table rules (WCAG 1.3.1)
    # -----------------------------------------------------------------------
    "TBL001": {
        "title": "Missing Table Headers",
        "wcag": "1.3.1",
        "severity": "error",
        "category": "tables",
        "description": "Data tables must have header cells (<th>) so screen readers can associate data with column/row labels.",
        "steps": [
            "Identify the header row (usually the first row).",
            "In the Rich Content Editor, select the header cells.",
            "Change them from regular cells to header cells (td -> th).",
            "Use the table properties dialog to mark the first row as a header.",
        ],
        "auto_fixable": True,
        "resources": [
            "https://www.w3.org/WAI/WCAG22/Understanding/info-and-relationships",
            "https://www.w3.org/WAI/tutorials/tables/",
        ],
    },
    "TBL002": {
        "title": "Missing Scope Attribute on Headers",
        "wcag": "1.3.1",
        "severity": "warning",
        "category": "tables",
        "description": "Table header cells should have a scope attribute (col or row) to clarify their direction.",
        "steps": [
            "Add scope='col' to column headers.",
            "Add scope='row' to row headers.",
            "This helps screen readers announce headers correctly when navigating cells.",
        ],
        "auto_fixable": True,
        "resources": [
            "https://www.w3.org/WAI/WCAG22/Understanding/info-and-relationships",
        ],
    },
    "TBL003": {
        "title": "Missing Table Caption",
        "wcag": "1.3.1",
        "severity": "warning",
        "category": "tables",
        "description": "Tables should have a caption that describes the table's purpose.",
        "steps": [
            "Add a <caption> element as the first child of the table.",
            "Write a brief description of what the table contains.",
            "In the Rich Content Editor, use the table properties to add a caption.",
        ],
        "auto_fixable": True,
        "resources": [
            "https://www.w3.org/WAI/WCAG22/Understanding/info-and-relationships",
        ],
    },
    "TBL004": {
        "title": "Empty Table Header",
        "wcag": "1.3.1",
        "severity": "error",
        "category": "tables",
        "description": "A table header cell is empty. Header cells must contain descriptive text.",
        "steps": [
            "Add descriptive text to the empty header cell.",
            "If the cell is not a header, change it to a regular data cell.",
        ],
        "auto_fixable": True,
        "resources": [
            "https://www.w3.org/WAI/WCAG22/Understanding/info-and-relationships",
        ],
    },
    "TBL005": {
        "title": "Layout Table Detected",
        "wcag": "1.3.1",
        "severity": "warning",
        "category": "tables",
        "description": "A table appears to be used for layout rather than data. Use CSS for layout instead.",
        "steps": [
            "If the table contains data, add proper headers.",
            "If used for layout, replace the table with CSS flexbox or grid.",
            "If it must remain a table, add role='presentation' to indicate it is not a data table.",
        ],
        "auto_fixable": True,
        "resources": [
            "https://www.w3.org/WAI/WCAG22/Understanding/info-and-relationships",
        ],
    },
    "TBL006": {
        "title": "Sparse/Near-Empty Table",
        "wcag": "1.3.1",
        "severity": "warning",
        "category": "tables",
        "description": "The table has many empty cells, suggesting it may not be the best way to present this content.",
        "steps": [
            "Consider whether a list or simple paragraphs would work better.",
            "If the table is needed, fill empty cells or merge them.",
        ],
        "auto_fixable": True,
        "resources": [
            "https://www.w3.org/WAI/WCAG22/Understanding/info-and-relationships",
        ],
    },
    # -----------------------------------------------------------------------
    # Link rules (WCAG 2.4.4)
    # -----------------------------------------------------------------------
    "LNK001": {
        "title": "Non-Descriptive Link Text",
        "wcag": "2.4.4",
        "severity": "warning",
        "category": "links",
        "description": "Link text like 'click here' or 'read more' does not describe the destination. Links should make sense out of context.",
        "steps": [
            "Rewrite the link text to describe where the link goes.",
            "Replace 'Click here to view the syllabus' with 'View the course syllabus'.",
            "Avoid generic phrases: 'here', 'click here', 'read more', 'learn more'.",
        ],
        "auto_fixable": True,
        "resources": [
            "https://www.w3.org/WAI/WCAG22/Understanding/link-purpose-in-context",
        ],
    },
    "LNK002": {
        "title": "Redundant Title Attribute on Link",
        "wcag": "2.4.4",
        "severity": "warning",
        "category": "links",
        "description": "The link's title attribute duplicates the link text. This creates unnecessary repetition for screen readers.",
        "steps": [
            "Remove the title attribute from the link.",
            "Title attributes are generally not needed when link text is descriptive.",
        ],
        "auto_fixable": True,
        "resources": [
            "https://www.w3.org/WAI/WCAG22/Understanding/link-purpose-in-context",
        ],
    },
    "LNK003": {
        "title": "Empty Link",
        "wcag": "2.4.4",
        "severity": "error",
        "category": "links",
        "description": "A link has no text content. Screen reader users will not know where the link goes.",
        "steps": [
            "Add descriptive text inside the link.",
            "If the link wraps an image, add alt text to the image.",
            "If the link is not needed, remove it.",
        ],
        "auto_fixable": True,
        "resources": [
            "https://www.w3.org/WAI/WCAG22/Understanding/link-purpose-in-context",
        ],
    },
    "LNK004": {
        "title": "Fake Link (Styled Text, Not a Real Link)",
        "wcag": "2.4.4",
        "severity": "warning",
        "category": "links",
        "description": "Text is styled to look like a link (underlined, colored) but is not an actual hyperlink.",
        "steps": [
            "If the text should be a link, wrap it in an <a> tag with a valid href.",
            "If it should not be a link, remove the link-like styling.",
        ],
        "auto_fixable": False,
        "resources": [
            "https://www.w3.org/WAI/WCAG22/Understanding/link-purpose-in-context",
        ],
    },
    "LNK005": {
        "title": "Raw URL as Link Text",
        "wcag": "2.4.4",
        "severity": "warning",
        "category": "links",
        "description": "The link text is a raw URL. This is hard to understand, especially for screen reader users.",
        "steps": [
            "Replace the URL text with a descriptive label.",
            "Example: Change 'https://example.com/report.pdf' to 'Quarterly Report (PDF)'.",
        ],
        "auto_fixable": False,
        "resources": [
            "https://www.w3.org/WAI/WCAG22/Understanding/link-purpose-in-context",
        ],
    },
    "LNK006": {
        "title": "Adjacent Duplicate Links",
        "wcag": "2.4.4",
        "severity": "warning",
        "category": "links",
        "description": "Two adjacent links point to the same destination. Combine them into a single link.",
        "steps": [
            "Merge the two links into one link that contains both the text and any image.",
            "This reduces tab stops and avoids confusing repetition.",
        ],
        "auto_fixable": True,
        "resources": [
            "https://www.w3.org/WAI/WCAG22/Understanding/link-purpose-in-context",
        ],
    },
    "LNK007": {
        "title": "Duplicate Link Text with Different Destinations",
        "wcag": "2.4.4",
        "severity": "info",
        "category": "links",
        "description": "Multiple links have the same text but go to different URLs.",
        "steps": [
            "Give each link unique, descriptive text.",
            "Add context to distinguish them (e.g., 'Module 1 Assignment' vs 'Module 2 Assignment').",
        ],
        "auto_fixable": False,
        "resources": [
            "https://www.w3.org/WAI/WCAG22/Understanding/link-purpose-in-context",
        ],
    },
    "LNK008": {
        "title": "Document Link Without Format Indicator",
        "wcag": "2.4.4",
        "severity": "warning",
        "category": "links",
        "description": "A link points to a document (PDF, DOCX, etc.) but the link text does not indicate the file type.",
        "steps": [
            "Add the file format to the link text, e.g., 'Syllabus (PDF)'.",
            "This helps users know what to expect before clicking.",
        ],
        "auto_fixable": True,
        "resources": [
            "https://www.w3.org/WAI/WCAG22/Understanding/link-purpose-in-context",
        ],
    },
    "LNK009": {
        "title": "Broken Link (Space in URL)",
        "wcag": "2.4.4",
        "severity": "error",
        "category": "links",
        "description": "The link URL contains unencoded spaces, which may cause it to break.",
        "steps": [
            "Replace spaces in the URL with %20 or re-paste the correct URL.",
            "Test the link to ensure it works.",
        ],
        "auto_fixable": True,
        "resources": [
            "https://www.w3.org/WAI/WCAG22/Understanding/link-purpose-in-context",
        ],
    },
    "LNK010": {
        "title": "Broken Same-Page Anchor Link",
        "wcag": "2.4.4",
        "severity": "warning",
        "category": "links",
        "description": "An anchor link (#id) points to an element that does not exist on the page.",
        "steps": [
            "Verify the target element has the correct id attribute.",
            "Fix the href to match the target element's id.",
            "Remove the link if the target no longer exists.",
        ],
        "auto_fixable": True,
        "resources": [
            "https://www.w3.org/WAI/WCAG22/Understanding/link-purpose-in-context",
        ],
    },
    # -----------------------------------------------------------------------
    # Contrast rules (WCAG 1.4.3)
    # -----------------------------------------------------------------------
    "CLR001": {
        "title": "Insufficient Color Contrast",
        "wcag": "1.4.3",
        "severity": "error",
        "category": "contrast",
        "description": "Text does not have sufficient contrast against its background. WCAG AA requires 4.5:1 for normal text and 3:1 for large text.",
        "steps": [
            "Use the Remedy Canvas LTI contrast checker to test your color combinations.",
            "Darken the text color or lighten the background to meet the 4.5:1 ratio.",
            "For large text (18pt+ or 14pt+ bold), a 3:1 ratio is sufficient.",
            "Avoid placing text on complex backgrounds (images, gradients).",
        ],
        "auto_fixable": True,
        "resources": [
            "https://www.w3.org/WAI/WCAG22/Understanding/contrast-minimum",
            "https://webaim.org/resources/contrastchecker/",
        ],
    },
    # -----------------------------------------------------------------------
    # Structure rules (WCAG 1.3.1, 4.1.2, 1.4.4)
    # -----------------------------------------------------------------------
    "LIST001": {
        "title": "Content That Should Be a List",
        "wcag": "1.3.1",
        "severity": "warning",
        "category": "structure",
        "description": "Content appears to be a list (using dashes, asterisks, numbers) but is not marked up as a list element.",
        "steps": [
            "Select the list-like content.",
            "Use the Rich Content Editor's list buttons to convert it to a proper <ul> or <ol>.",
            "This helps screen readers announce the number of items and navigate between them.",
        ],
        "auto_fixable": True,
        "resources": [
            "https://www.w3.org/WAI/WCAG22/Understanding/info-and-relationships",
        ],
    },
    "STR001": {
        "title": "Empty Elements",
        "wcag": "1.3.1",
        "severity": "warning",
        "category": "structure",
        "description": "Empty elements (empty paragraphs, divs) add unnecessary clutter for screen reader users.",
        "steps": [
            "Remove empty paragraphs and divs.",
            "Use CSS margin/padding for spacing instead of empty elements.",
        ],
        "auto_fixable": True,
        "resources": [
            "https://www.w3.org/WAI/WCAG22/Understanding/info-and-relationships",
        ],
    },
    "STR002": {
        "title": "Deprecated HTML Tags",
        "wcag": "4.1.1",
        "severity": "error",
        "category": "structure",
        "description": "The page uses deprecated HTML tags (e.g., <font>, <center>, <marquee>) that may not be supported.",
        "steps": [
            "Replace <font> with CSS font styling.",
            "Replace <center> with CSS text-align: center.",
            "Remove <marquee> elements entirely (they cause accessibility issues).",
        ],
        "auto_fixable": True,
        "resources": [
            "https://www.w3.org/WAI/WCAG22/Understanding/parsing",
        ],
    },
    "STR003": {
        "title": "Small Text (Below Minimum Size)",
        "wcag": "1.4.4",
        "severity": "warning",
        "category": "structure",
        "description": "Text is smaller than 10px, which may be difficult to read.",
        "steps": [
            "Increase the font size to at least 12px (preferably 16px for body text).",
            "Use relative units (em, rem) instead of absolute pixels.",
        ],
        "auto_fixable": True,
        "resources": [
            "https://www.w3.org/WAI/WCAG22/Understanding/resize-text",
        ],
    },
    "ARIA001": {
        "title": "Broken ARIA Reference",
        "wcag": "4.1.2",
        "severity": "error",
        "category": "structure",
        "description": "An ARIA attribute (aria-labelledby, aria-describedby, etc.) references an ID that does not exist.",
        "steps": [
            "Check the referenced id exists on the page.",
            "Fix the ARIA attribute to point to the correct id.",
            "Remove the ARIA attribute if the referenced element no longer exists.",
        ],
        "auto_fixable": True,
        "resources": [
            "https://www.w3.org/WAI/WCAG22/Understanding/name-role-value",
        ],
    },
    "DID001": {
        "title": "Duplicate ID Attributes",
        "wcag": "4.1.1",
        "severity": "error",
        "category": "structure",
        "description": "The same id attribute value appears on multiple elements. IDs must be unique within a page for ARIA references, labels, and JavaScript to work correctly.",
        "steps": [
            "Find all elements with the duplicate id.",
            "Rename each duplicate to a unique value (e.g. append -2, -3).",
            "Update any aria-labelledby, aria-describedby, or label[for] references.",
        ],
        "auto_fixable": True,
        "resources": [
            "https://www.w3.org/WAI/WCAG22/Understanding/parsing",
        ],
    },
    "ARIA002": {
        "title": "aria-hidden on Focusable Content",
        "wcag": "4.1.2",
        "severity": "error",
        "category": "structure",
        "description": "aria-hidden='true' is set on an element that contains focusable children (links, buttons, inputs). Keyboard users can reach these elements, but screen readers cannot announce them.",
        "steps": [
            "Remove aria-hidden='true' if the content should be visible to all users.",
            "If the content is truly decorative, also remove the focusable elements or add tabindex='-1' to each.",
        ],
        "auto_fixable": False,
        "resources": [
            "https://www.w3.org/WAI/WCAG22/Understanding/name-role-value",
        ],
    },
    "ARIA003": {
        "title": "Missing Required ARIA Property",
        "wcag": "4.1.2",
        "severity": "error",
        "category": "structure",
        "description": "An element with an explicit ARIA role is missing a property that the role requires. For example, role='slider' requires aria-valuenow.",
        "steps": [
            "Check the WAI-ARIA spec for the role's required properties.",
            "Add the missing property with an appropriate value.",
            "If unsure of the value, consider using a native HTML element instead.",
        ],
        "auto_fixable": False,
        "resources": [
            "https://www.w3.org/WAI/WCAG22/Understanding/name-role-value",
            "https://www.w3.org/TR/wai-aria-1.2/#role_definitions",
        ],
    },
    "ARIA004": {
        "title": "Invalid ARIA Role",
        "wcag": "4.1.2",
        "severity": "error",
        "category": "structure",
        "description": "An element has a role attribute with a value that is not a valid WAI-ARIA role. This is often a typo.",
        "steps": [
            "Check the role value for typos (e.g. 'buton' to 'button').",
            "Replace with a valid WAI-ARIA role or remove the attribute.",
            "Consider using a native HTML element instead of a role on a div/span.",
        ],
        "auto_fixable": True,
        "resources": [
            "https://www.w3.org/WAI/WCAG22/Understanding/name-role-value",
            "https://www.w3.org/TR/wai-aria-1.2/#role_definitions",
        ],
    },
    "FORM009": {
        "title": "Missing Autocomplete for Personal Data",
        "wcag": "1.3.5",
        "severity": "warning",
        "category": "forms",
        "description": "A form input appears to collect personal information (name, email, phone, address) but has no autocomplete attribute. This helps users with cognitive disabilities by enabling auto-fill.",
        "steps": [
            "Identify the type of personal data the input collects.",
            "Add the appropriate autocomplete value (e.g. autocomplete='email').",
            "See the WCAG 1.3.5 input purposes list for all valid values.",
        ],
        "auto_fixable": True,
        "resources": [
            "https://www.w3.org/WAI/WCAG22/Understanding/identify-input-purpose",
            "https://www.w3.org/TR/WCAG22/#input-purposes",
        ],
    },
    # -----------------------------------------------------------------------
    # Media rules (WCAG 1.2.2, 1.4.2)
    # -----------------------------------------------------------------------
    "MDA001": {
        "title": "Media Without Captions",
        "wcag": "1.2.2",
        "severity": "error",
        "category": "media",
        "description": "Video or audio content does not appear to have captions or a transcript.",
        "steps": [
            "Add closed captions to videos (use Canvas Studio or YouTube).",
            "Provide a transcript link near audio content.",
            "For live media, provide real-time captions.",
        ],
        "auto_fixable": False,
        "resources": [
            "https://www.w3.org/WAI/WCAG22/Understanding/captions-prerecorded",
        ],
    },
    "MDA002": {
        "title": "Autoplay Media",
        "wcag": "1.4.2",
        "severity": "error",
        "category": "media",
        "description": "Media content auto-plays, which can be disorienting and interfere with screen readers.",
        "steps": [
            "Remove the autoplay attribute from video and audio elements.",
            "Let users control when media starts playing.",
        ],
        "auto_fixable": True,
        "resources": [
            "https://www.w3.org/WAI/WCAG22/Understanding/audio-control",
        ],
    },
    # -----------------------------------------------------------------------
    # Math rules (WCAG 1.3.1)
    # -----------------------------------------------------------------------
    "MATH001": {
        "title": "LaTeX/Plain Text Math Expressions",
        "wcag": "1.3.1",
        "severity": "warning",
        "category": "math",
        "description": "Math expressions appear as plain text or LaTeX, which screen readers cannot interpret correctly.",
        "steps": [
            "Convert LaTeX expressions to MathML or use the Canvas equation editor.",
            "Use MathJax to render LaTeX as accessible MathML.",
            "Ensure equations have text alternatives for complex expressions.",
        ],
        "auto_fixable": True,
        "resources": [
            "https://www.w3.org/WAI/WCAG22/Understanding/info-and-relationships",
        ],
    },
    # -----------------------------------------------------------------------
    # Target size rules (WCAG 2.5.8)
    # -----------------------------------------------------------------------
    "TGT001": {
        "title": "Target Size Too Small",
        "wcag": "2.5.8",
        "severity": "warning",
        "category": "focus",
        "description": "Interactive elements (links, buttons) are smaller than the 24x24px minimum target size.",
        "steps": [
            "Increase the padding around small interactive elements.",
            "Ensure touch targets are at least 24x24 CSS pixels.",
            "Add spacing between closely-placed interactive elements.",
        ],
        "auto_fixable": True,
        "resources": [
            "https://www.w3.org/WAI/WCAG22/Understanding/target-size-minimum",
        ],
    },
    # -----------------------------------------------------------------------
    # WCAG 2.2 rules
    # -----------------------------------------------------------------------
    "FOC001": {
        "title": "Focus Not Obscured",
        "wcag": "2.4.11",
        "severity": "warning",
        "category": "focus",
        "description": "Elements with fixed/sticky positioning may obscure focused elements.",
        "steps": [
            "Ensure fixed/sticky elements do not cover focusable content.",
            "Add scroll padding to account for fixed headers.",
            "Test keyboard navigation to verify focus indicators are always visible.",
        ],
        "auto_fixable": True,
        "resources": [
            "https://www.w3.org/WAI/WCAG22/Understanding/focus-not-obscured-minimum",
        ],
    },
    "HELP001": {
        "title": "Inconsistent Help Mechanism",
        "wcag": "3.2.6",
        "severity": "info",
        "category": "structure",
        "description": (
            "Other pages in this course share a help link in their footer, "
            "but this page is missing it. WCAG 3.2.6 requires that help "
            "mechanisms appear in a consistent relative order across pages."
        ),
        "steps": [
            "Copy the help link from the footer of another page onto this one.",
            "Place it in the same relative position — typically the last paragraph or inside a footer element.",
            "Keep the link text and href identical to the course convention.",
        ],
        "auto_fixable": False,
        "resources": [
            "https://www.w3.org/WAI/WCAG22/Understanding/consistent-help",
        ],
    },
    "FORM001": {
        "title": "Redundant Entry Required",
        "wcag": "3.3.7",
        "severity": "warning",
        "category": "forms",
        "description": "Users may be required to re-enter information that was previously provided.",
        "steps": [
            "Auto-populate fields with previously entered information.",
            "Allow users to select from previously entered values.",
            "Use autocomplete attributes on form fields.",
        ],
        "auto_fixable": False,
        "resources": [
            "https://www.w3.org/WAI/WCAG22/Understanding/redundant-entry",
        ],
    },
    "AUTH001": {
        "title": "Accessible Authentication",
        "wcag": "3.3.8",
        "severity": "warning",
        "category": "forms",
        "description": "Authentication should not require cognitive function tests (e.g., CAPTCHA) without alternatives.",
        "steps": [
            "Provide alternatives to cognitive tests (copy-paste, biometrics).",
            "If CAPTCHA is used, provide an audio alternative.",
            "Allow password managers and paste into authentication fields.",
        ],
        "auto_fixable": True,
        "resources": [
            "https://www.w3.org/WAI/WCAG22/Understanding/accessible-authentication-minimum",
        ],
    },
    # -----------------------------------------------------------------------
    # Additional image rules (WCAG 1.1.1)
    # -----------------------------------------------------------------------
    "IMG006": {
        "title": "Image Has Title But No Alt Text",
        "wcag": "1.1.1",
        "severity": "warning",
        "category": "images",
        "description": "An image has a title attribute but no alt text. The title attribute is not a substitute for alt text.",
        "steps": [
            "Add a descriptive alt attribute to the image.",
            "If the title text is appropriate, copy it to the alt attribute.",
            "Remove the title attribute if it duplicates the new alt text.",
        ],
        "auto_fixable": True,
        "resources": [
            "https://www.w3.org/WAI/WCAG22/Understanding/non-text-content",
        ],
    },
    "IMG008": {
        "title": "Invalid Longdesc Attribute",
        "wcag": "1.1.1",
        "severity": "error",
        "category": "images",
        "description": "The longdesc attribute value is not a valid URL. It must point to a page containing a long description of the image.",
        "steps": [
            "Verify the longdesc value is a valid URL (starts with http, /, or #).",
            "Fix or remove the longdesc attribute if it contains plain text instead of a URL.",
            "Consider using aria-describedby as a modern alternative to longdesc.",
        ],
        "auto_fixable": False,
        "resources": [
            "https://www.w3.org/WAI/WCAG22/Understanding/non-text-content",
        ],
    },
    "IMG009": {
        "title": "Suspicious Alt Text",
        "wcag": "1.1.1",
        "severity": "warning",
        "category": "images",
        "description": "Alt text contains redundant words like 'image of' or 'picture of'. Screen readers already announce the element as an image.",
        "steps": [
            "Remove leading phrases like 'Image of', 'Picture of', 'Photo of' from the alt text.",
            "Remove trailing noise words like '... image', '... photo' from the alt text.",
            "Describe what the image conveys, not what it is.",
        ],
        "auto_fixable": True,
        "resources": [
            "https://www.w3.org/WAI/WCAG22/Understanding/non-text-content",
            "https://www.w3.org/WAI/tutorials/images/tips/",
        ],
    },
    # -----------------------------------------------------------------------
    # Additional structure rules (WCAG 1.3.1, 1.4.8)
    # -----------------------------------------------------------------------
    "STR004": {
        "title": "Underlined Text (Not a Link)",
        "wcag": "1.3.1",
        "severity": "warning",
        "category": "structure",
        "description": "Underlined text that is not a link can confuse users who expect underlines to indicate clickable links.",
        "steps": [
            "Replace underline formatting with bold or italic for emphasis.",
            "If using the <u> tag, replace it with <em> or <strong>.",
            "Remove text-decoration: underline from inline styles on non-link elements.",
        ],
        "auto_fixable": True,
        "resources": [
            "https://www.w3.org/WAI/WCAG22/Understanding/info-and-relationships",
        ],
    },
    "STR005": {
        "title": "Justified Text",
        "wcag": "1.4.8",
        "severity": "warning",
        "category": "structure",
        "description": "Justified text creates uneven spacing between words, making it harder to read for people with dyslexia or other reading disabilities.",
        "steps": [
            "Change text-align: justify to text-align: left in the element's style.",
            "Use left-aligned text for body content.",
            "If justified text is required for design, provide a way to switch to left-aligned.",
        ],
        "auto_fixable": True,
        "resources": [
            "https://www.w3.org/WAI/WCAG22/Understanding/visual-presentation",
        ],
    },
    # -----------------------------------------------------------------------
    # Additional media rules (WCAG 1.2.2)
    # -----------------------------------------------------------------------
    "MDA003": {
        "title": "YouTube Video — Verify Captions",
        "wcag": "1.2.2",
        "severity": "warning",
        "category": "media",
        "description": "A YouTube video is embedded. Verify that captions are human-generated, not auto-generated, as auto-captions often contain errors.",
        "steps": [
            "Open the YouTube video and check if captions are available.",
            "Verify captions are not auto-generated (auto-captions are labeled 'auto-generated' in YouTube).",
            "If only auto-captions exist, upload corrected captions or a transcript.",
        ],
        "auto_fixable": False,
        "resources": [
            "https://www.w3.org/WAI/WCAG22/Understanding/captions-prerecorded",
        ],
    },
    "MDA004": {
        "title": "YouTube Video — Verify Availability",
        "wcag": "1.2.2",
        "severity": "warning",
        "category": "media",
        "description": "A YouTube video is embedded. Verify that the video is still available and has not been removed or made private.",
        "steps": [
            "Click the video link or embed to confirm it loads correctly.",
            "If the video is unavailable, find a replacement or remove the embed.",
            "Update the link text or surrounding context if the video has changed.",
        ],
        "auto_fixable": False,
        "resources": [
            "https://www.w3.org/WAI/WCAG22/Understanding/captions-prerecorded",
        ],
    },
    "MDA005": {
        "title": "Canvas Studio Video — Verify Captions",
        "wcag": "1.2.2",
        "severity": "warning",
        "category": "media",
        "description": "A Canvas Studio video is embedded. Verify that captions are human-generated, not auto-generated.",
        "steps": [
            "Open the Canvas Studio video and check caption status.",
            "If only auto-generated captions exist, edit them for accuracy or upload a corrected caption file.",
            "Ensure captions are synchronized with the audio.",
        ],
        "auto_fixable": False,
        "resources": [
            "https://www.w3.org/WAI/WCAG22/Understanding/captions-prerecorded",
        ],
    },
    "MDA006": {
        "title": "Canvas Studio Video — Verify Availability",
        "wcag": "1.2.2",
        "severity": "warning",
        "category": "media",
        "description": "A Canvas Studio video is embedded. Verify that the video is still available and accessible.",
        "steps": [
            "Click the video embed to confirm it loads correctly.",
            "If the video is unavailable, contact the video owner or find a replacement.",
            "Remove broken embeds that reference deleted videos.",
        ],
        "auto_fixable": False,
        "resources": [
            "https://www.w3.org/WAI/WCAG22/Understanding/captions-prerecorded",
        ],
    },
    # -----------------------------------------------------------------------
    # Additional link rules (WCAG 2.4.4)
    # -----------------------------------------------------------------------
    "LNK011": {
        "title": "Redundant Empty Link",
        "wcag": "2.4.4",
        "severity": "warning",
        "category": "links",
        "description": "An empty link is redundant with another visible link pointing to the same destination. The empty link adds an extra tab stop without benefit.",
        "steps": [
            "Remove the empty link — the visible link already provides access to the destination.",
            "If the empty link has an aria-label, move that context to the visible link if needed.",
        ],
        "auto_fixable": True,
        "resources": [
            "https://www.w3.org/WAI/WCAG22/Understanding/link-purpose-in-context",
        ],
    },
    "LNK012": {
        "title": "Adjacent Image-Link and Text-Link to Same Destination",
        "wcag": "2.4.4",
        "severity": "warning",
        "category": "links",
        "description": "An image-only link and a text-only link point to the same URL and are adjacent. Merge them into a single link to reduce tab stops.",
        "steps": [
            "Combine the image and text into a single <a> element.",
            "Use the text from the text link as the alt text for the image.",
            "Remove the redundant second link.",
        ],
        "auto_fixable": True,
        "resources": [
            "https://www.w3.org/WAI/WCAG22/Understanding/link-purpose-in-context",
        ],
    },
    # -----------------------------------------------------------------------
    # Form rules (WCAG 1.3.1)
    # -----------------------------------------------------------------------
    "FORM003": {
        "title": "Empty Form Label",
        "wcag": "1.3.1",
        "severity": "error",
        "category": "forms",
        "description": "A form label element is present but contains no text. Labels must have descriptive text to identify the associated form control.",
        "steps": [
            "Add descriptive text to the empty <label> element.",
            "If the label is not needed, remove it.",
            "Ensure the label's for attribute matches the associated input's id.",
        ],
        "auto_fixable": True,
        "resources": [
            "https://www.w3.org/WAI/WCAG22/Understanding/info-and-relationships",
        ],
    },
    "FORM005": {
        "title": "Missing Fieldset for Radio/Checkbox Group",
        "wcag": "1.3.1",
        "severity": "error",
        "category": "forms",
        "description": "Radio buttons or checkboxes are not enclosed in a <fieldset> with a <legend>. Grouping helps screen reader users understand related controls.",
        "steps": [
            "Wrap related radio buttons or checkboxes in a <fieldset> element.",
            "Add a <legend> inside the fieldset that describes the group.",
            "Ensure each input still has its own <label>.",
        ],
        "auto_fixable": False,
        "resources": [
            "https://www.w3.org/WAI/WCAG22/Understanding/info-and-relationships",
            "https://www.w3.org/WAI/tutorials/forms/grouping/",
        ],
    },
    "FORM007": {
        "title": "Orphaned Form Label",
        "wcag": "1.3.1",
        "severity": "error",
        "category": "forms",
        "description": "A label's for attribute references an id that does not exist on the page. The label is not associated with any form control.",
        "steps": [
            "Check the label's for attribute and ensure a matching id exists on a form control.",
            "Fix the id on the target input to match the label's for value.",
            "Remove the for attribute if the label wraps the input directly.",
        ],
        "auto_fixable": True,
        "resources": [
            "https://www.w3.org/WAI/WCAG22/Understanding/info-and-relationships",
        ],
    },
    # -----------------------------------------------------------------------
    # Button rules (WCAG 1.1.1)
    # -----------------------------------------------------------------------
    "BTN001": {
        "title": "Empty Button",
        "wcag": "1.1.1",
        "severity": "error",
        "category": "structure",
        "description": "A button element contains no text, aria-label, or image with alt text. Screen reader users cannot determine the button's purpose.",
        "steps": [
            "Add descriptive text inside the button.",
            "Alternatively, add an aria-label attribute that describes the button's action.",
            "If the button contains an icon image, add alt text to the image.",
        ],
        "auto_fixable": True,
        "resources": [
            "https://www.w3.org/WAI/WCAG22/Understanding/non-text-content",
        ],
    },
    # -----------------------------------------------------------------------
    # Document rules (WCAG 1.1.1)
    # -----------------------------------------------------------------------
    "DOC001": {
        "title": "Link to Google Document",
        "wcag": "1.1.1",
        "severity": "warning",
        "category": "documents",
        "description": "A link points to a Google document. Verify the document meets accessibility standards (headings, alt text, reading order).",
        "steps": [
            "Open the Google document and check for accessibility issues.",
            "Ensure the document has proper heading structure and alt text for images.",
            "Consider converting the content to an accessible HTML page if possible.",
        ],
        "auto_fixable": False,
        "resources": [
            "https://www.w3.org/WAI/WCAG22/Understanding/non-text-content",
        ],
    },
    "DOC002": {
        "title": "Link to Canvas Asset",
        "wcag": "1.1.1",
        "severity": "info",
        "category": "documents",
        "description": "A link points to a Canvas file or media asset. Consider converting the content to an accessible HTML page.",
        "steps": [
            "Review the linked asset for accessibility.",
            "If it is a document (PDF, DOCX), ensure it is tagged and accessible.",
            "Consider converting the content to an HTML page within the course.",
        ],
        "auto_fixable": False,
        "resources": [
            "https://www.w3.org/WAI/WCAG22/Understanding/non-text-content",
        ],
    },
    "PDF001": {
        "title": "Link to PDF Document",
        "wcag": "1.1.1",
        "severity": "warning",
        "category": "documents",
        "description": "A link points to a PDF document. Verify the PDF is accessible (tagged, proper reading order, alt text for images).",
        "steps": [
            "Open the PDF and check that it is tagged (not a scanned image).",
            "Verify the reading order is logical and headings are properly structured.",
            "Ensure all images in the PDF have alt text.",
            "Consider converting the PDF to an accessible HTML page.",
        ],
        "auto_fixable": False,
        "resources": [
            "https://www.w3.org/WAI/WCAG22/Understanding/non-text-content",
        ],
    },
    # -----------------------------------------------------------------------
    # Event handler rules (WCAG 2.1.1)
    # -----------------------------------------------------------------------
    "EVT001": {
        "title": "Device-Dependent Event Handler",
        "wcag": "2.1.1",
        "severity": "warning",
        "category": "events",
        "description": "An element uses mouse-only event handlers (e.g., onmouseover, onmouseout) without keyboard equivalents. Keyboard users cannot trigger these interactions.",
        "steps": [
            "Add keyboard equivalents for mouse event handlers (e.g., onfocus for onmouseover, onblur for onmouseout).",
            "Ensure all interactive behavior is available via keyboard.",
            "Test the page using only the keyboard to verify all interactions work.",
        ],
        "auto_fixable": False,
        "resources": [
            "https://www.w3.org/WAI/WCAG22/Understanding/keyboard",
        ],
    },
    "EVT002": {
        "title": "JavaScript Jump Menu",
        "wcag": "2.1.1",
        "severity": "warning",
        "category": "events",
        "description": "A select element navigates to a new page on change. Keyboard users may trigger navigation unintentionally while scrolling through options.",
        "steps": [
            "Add a separate 'Go' button next to the select element instead of navigating on change.",
            "Remove the onchange navigation handler from the select element.",
            "Ensure users can review their selection before navigating.",
        ],
        "auto_fixable": False,
        "resources": [
            "https://www.w3.org/WAI/WCAG22/Understanding/keyboard",
        ],
    },
}


def get_guide(rule_id: str) -> dict | None:
    """Return the remediation guide for a specific rule, or None if not found."""
    return REMEDIATION_GUIDES.get(rule_id)


def get_all_guides() -> dict[str, dict]:
    """Return all remediation guides."""
    return REMEDIATION_GUIDES
