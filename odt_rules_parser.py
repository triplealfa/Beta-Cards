# Copyright (C) 2026 Triple Alfa
# SPDX-License-Identifier: GPL-3.0-or-later
#
# This file is part of Beta Cards.
#
# Beta Cards is free software: you can redistribute it and/or modify it under
# the terms of the GNU General Public License as published by the Free Software
# Foundation, either version 3 of the License, or (at your option) any later
# version.
#
# Beta Cards is distributed in the hope that it will be useful, but WITHOUT ANY
# WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
# PARTICULAR PURPOSE. See the GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License along with
# Beta Cards. If not, see <https://www.gnu.org/licenses/>.

"""Parse ODT files and convert to HTML for display."""

import html
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET


def load_rules_as_html(odt_path: Path) -> str:
    """Load an ODT file and convert its content to HTML.
    
    Args:
        odt_path: Path to the .odt file
        
    Returns:
        HTML string with formatting preserved (bold, italic, headings, etc.)
    """
    if not odt_path.exists():
        return "<p>Rules file not found.</p>"
    
    try:
        with zipfile.ZipFile(odt_path, 'r') as odt_zip:
            content_xml = odt_zip.read('content.xml')
            
        return _parse_odt_content_to_html(content_xml)
    except Exception as e:
        return f"<p>Error loading rules: {html.escape(str(e))}</p>"


def _parse_odt_content_to_html(content_xml: bytes) -> str:
    """Parse ODT content.xml to HTML.
    
    Converts ODT formatting to HTML while preserving:
    - Bold, italic, underline
    - Headings
    - Lists and list items
    - Paragraphs
    """
    try:
        # Define namespaces
        namespaces = {
            'text': 'urn:oasis:names:tc:opendocument:xmlns:text:1.0',
            'office': 'urn:oasis:names:tc:opendocument:xmlns:office:1.0',
            'style': 'urn:oasis:names:tc:opendocument:xmlns:style:1.0',
            'fo': 'urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0',
        }
        
        root = ET.fromstring(content_xml)
        
        # Load automatic styles (both text and paragraph)
        text_style_cache, para_style_cache = _load_automatic_styles(root, namespaces)
        
        body = root.find('.//office:body', namespaces)
        
        if body is None:
            return "<p>No content found in ODT.</p>"
        
        text_body = body.find('office:text', namespaces)
        if text_body is None:
            return "<p>No text found in ODT.</p>"
        
        html_parts = ['<div style="font-family: Liberation Serif, serif; font-size: 15pt; line-height: 1.4; text-rendering: optimizeLegibility; -webkit-font-smoothing: antialiased; -moz-osx-font-smoothing: grayscale;">']
        
        for element in text_body:
            html_parts.append(_convert_element_to_html(element, namespaces, text_style_cache, para_style_cache))
        
        html_parts.append('</div>')
        return ''.join(html_parts)
        
    except Exception as e:
        return f"<p>Error parsing ODT: {html.escape(str(e))}</p>"


def _load_automatic_styles(root, namespaces: dict) -> tuple:
    """Load automatic styles from the ODT and cache their properties.
    
    Returns:
        Tuple of (text_style_cache, paragraph_style_cache)
    """
    text_style_cache = {}
    paragraph_style_cache = {}
    
    style_ns = namespaces.get('style', 'urn:oasis:names:tc:opendocument:xmlns:style:1.0')
    fo_ns = namespaces.get('fo', 'urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0')
    
    auto_styles = root.find('.//office:automatic-styles', namespaces)
    if auto_styles is None:
        return text_style_cache, paragraph_style_cache
    
    for style_elem in auto_styles.findall('style:style', namespaces):
        style_name = style_elem.get(f'{{{style_ns}}}name')
        family = style_elem.get(f'{{{style_ns}}}family')
        
        if not style_name:
            continue
        
        # Load text styles
        if family == 'text':
            text_props = style_elem.find('style:text-properties', namespaces)
            if text_props is not None:
                props = {}
                for key, value in text_props.attrib.items():
                    key_clean = key.split('}')[1] if '}' in key else key
                    props[key_clean] = value
                text_style_cache[style_name] = props
        
        # Load paragraph styles
        elif family == 'paragraph':
            props = {}
            text_props = style_elem.find('style:text-properties', namespaces)
            if text_props is not None:
                for key, value in text_props.attrib.items():
                    key_clean = key.split('}')[1] if '}' in key else key
                    props[key_clean] = value
            
            para_props = style_elem.find('style:paragraph-properties', namespaces)
            if para_props is not None:
                for key, value in para_props.attrib.items():
                    key_clean = key.split('}')[1] if '}' in key else key
                    props[key_clean] = value
            
            if props:
                paragraph_style_cache[style_name] = props
    
    return text_style_cache, paragraph_style_cache


def _convert_element_to_html(element, namespaces: dict, text_style_cache: dict, para_style_cache: dict) -> str:
    """Recursively convert an ODT element to HTML."""
    text_ns = namespaces.get('text', 'urn:oasis:names:tc:opendocument:xmlns:text:1.0')
    
    tag = _strip_namespace(element.tag)
    
    # Paragraphs
    if tag == 'p':
        # Get paragraph style
        para_style_name = element.get(f'{{{text_ns}}}style-name', '')
        para_props = para_style_cache.get(para_style_name, {})
        
        # Check if paragraph has spans
        has_spans = bool(element.findall(f'{{{text_ns}}}span'))
        
        content = _get_element_text_with_formatting(element, namespaces, text_style_cache, para_style_cache)
        
        # Apply paragraph-level formatting (pass info about spans to avoid overriding conflicts)
        para_html = _apply_paragraph_formatting(content, para_props, has_spans)
        
        # For empty paragraphs, use &nbsp; to preserve vertical spacing
        # Check if paragraph is truly empty (no text content, even if it has empty HTML tags)
        stripped = re.sub(r'<[^>]+>', '', para_html).strip()
        if not stripped:
            para_html = '&nbsp;'
        
        # Check if this is already a heading (div with font-size style) - if so, don't wrap in <p> tags
        if re.match(r'^<div[^>]*style=[^>]*font-size', para_html):
            return para_html
        else:
            # For regular paragraphs, apply font-size if it exists in properties
            p_style = 'margin: 0.3em 0;'
            font_size = para_props.get('font-size', '')
            if font_size and font_size not in [size for size, _ in [(s, (l, e)) for s, (l, e) in {
                '20pt': (1, '2.8em'), '18pt': (1, '2.6em'), '16pt': (2, '2.3em'), 
                '14pt': (3, '2.0em'), '22pt': (1, '3.0em'), '24pt': (1, '3.2em'), 
                '26pt': (1, '3.4em'), '28pt': (1, '3.6em')
            }.items()]]:
                # Convert to em units relative to 15pt base
                size_value = font_size.replace('pt', '')
                try:
                    pt_size = float(size_value)
                    em_size = pt_size / 15.0
                    p_style += f' font-size: {em_size:.2f}em;'
                except ValueError:
                    pass
            
            return f'<p style="{p_style}">{para_html}</p>'
    
    # Lists
    elif tag == 'list':
        items = []
        for list_item in element.findall(f'{{{text_ns}}}list-item'):
            item_content = _get_element_text_with_formatting(list_item, namespaces, text_style_cache, para_style_cache)
            items.append(f'<li style="margin: 0.3em 0;">{item_content}</li>')
        return f'<ul style="margin: 0.3em 0; padding-left: 2em;">{"".join(items)}</ul>'
    
    # Default: try to extract text
    else:
        return _get_element_text_with_formatting(element, namespaces, text_style_cache, para_style_cache)


def _get_element_text_with_formatting(element, namespaces: dict, text_style_cache: dict, para_style_cache: dict = None) -> str:
    """Extract text from an element with formatting (bold, italic, etc.).
    
    Args:
        element: The XML element to extract text from
        namespaces: XML namespaces
        text_style_cache: Cache of text (span) styles
        para_style_cache: Cache of paragraph styles (used for plain text formatting)
    """
    text_ns = namespaces.get('text', 'urn:oasis:names:tc:opendocument:xmlns:text:1.0')
    
    parts = []
    
    # Add element's own text, potentially with paragraph formatting
    if element.text and element.text.strip():
        text_content = html.escape(element.text)
        
        # If this is a paragraph element, try to apply paragraph formatting to plain text
        tag = _strip_namespace(element.tag)
        if para_style_cache and tag == 'p':
            para_style_name = element.get(f'{{{text_ns}}}style-name', '')
            para_props = para_style_cache.get(para_style_name, {})
            # Apply paragraph formatting to plain text portions
            text_content = _apply_char_formatting_from_props(text_content, para_props)
        
        parts.append(text_content)
    elif element.text:
        # Preserve whitespace if significant
        parts.append(html.escape(element.text))
    
    # Process child elements
    for child in element:
        tag = _strip_namespace(child.tag)
        
        if tag == 'span' or tag == 'text-span':
            # Get style name using TEXT namespace and look up formatting
            style_name = child.get(f'{{{text_ns}}}style-name', '')
            content = _get_element_text_with_formatting(child, namespaces, text_style_cache, para_style_cache)
            
            # Apply formatting based on span style cache
            if style_name and style_name in text_style_cache:
                content = _apply_style_properties(content, text_style_cache[style_name])
            
            parts.append(content)
        
        elif tag == 'tab':
            parts.append('&nbsp;&nbsp;&nbsp;&nbsp;')
        
        elif tag == 'line-break':
            parts.append('<br>')
        
        else:
            # Recursively process other elements
            parts.append(_get_element_text_with_formatting(child, namespaces, text_style_cache, para_style_cache))
        
        # Add tail text (text after the child element)
        if child.tail:
            parts.append(html.escape(child.tail))
    
    return ''.join(parts)


def _apply_char_formatting_from_props(content: str, properties: dict) -> str:
    """Apply character-level formatting from properties dict (used for plain text in paragraphs)."""
    is_bold = properties.get('font-weight', '').lower() in ['bold', '700', '800', '900']
    is_italic = properties.get('font-style', '').lower() == 'italic'
    
    if is_bold:
        content = f'<b>{content}</b>'
    if is_italic:
        content = f'<i>{content}</i>'
    
    return content


def _apply_style_properties(content: str, properties: dict) -> str:
    """Apply HTML formatting based on ODT style properties."""
    # Check for bold (font-weight: bold)
    is_bold = properties.get('font-weight', '').lower() in ['bold', '700', '800', '900']
    
    # Check for italic (font-style: italic)
    is_italic = properties.get('font-style', '').lower() == 'italic'
    
    # Check for underline
    underline_style = properties.get('text-underline-style', '').lower()
    is_underline = underline_style not in ['none', '']
    
    # Apply formatting tags
    if is_bold:
        content = f'<b>{content}</b>'
    if is_italic:
        content = f'<i>{content}</i>'
    if is_underline:
        content = f'<u>{content}</u>'
    
    return content


def _apply_paragraph_formatting(content: str, properties: dict, has_spans: bool = False) -> str:
    """Apply HTML formatting based on paragraph-level ODT style properties.
    
    Detects headings by font-size and applies bold/italic when appropriate.
    
    Args:
        content: The HTML content to format
        properties: The paragraph style properties
        has_spans: Whether the paragraph contains child spans that may override formatting
    """
    if not properties:
        return content
    
    # Check for heading by font-size (always apply)
    font_size = properties.get('font-size', '')
    
    # Map font sizes to heading levels and convert to px units for absolute control
    heading_levels = {
        '20pt': (1, '42px'),   # Main headings - largest
        '18pt': (1, '36px'),   # Large headings
        '16pt': (2, '28px'),   # Subheadings - clearly larger than regular text
        '14pt': (3, '20px'),   # Minor headings - still larger than regular text
        '22pt': (1, '46px'),   # Very large headings
        '24pt': (1, '50px'),   # Extra large headings
        '26pt': (1, '54px'),   # Huge headings
        '28pt': (1, '58px'),   # Massive headings
    }
    
    # Check if this is a heading
    for size, (level, em_size) in heading_levels.items():
        if font_size == size:
            # Use styled span instead of h1/h2/h3 to bypass Qt HTML renderer limitations
            # Keep headings visually distinct without stacking a large block margin
            # on top of the blank paragraph line that often follows in the source ODT.
            heading_html = (
                f'<div style="font-size: {em_size}; margin: 0.1em 0 0.08em 0; '
                f'font-weight: bold; line-height: 1.08;">{content}</div>'
            )
            return heading_html
    
    # Apply bold/italic only if there are no spans (no character-level overrides)
    # If there are spans, they should handle their own formatting
    if not has_spans:
        is_bold = properties.get('font-weight', '').lower() in ['bold', '700', '800', '900']
        is_italic = properties.get('font-style', '').lower() == 'italic'
        
        if is_bold:
            content = f'<b>{content}</b>'
        if is_italic:
            content = f'<i>{content}</i>'
    
    return content



def _strip_namespace(tag: str) -> str:
    """Remove XML namespace from tag."""
    if '}' in tag:
        return tag.split('}')[1]
    return tag
