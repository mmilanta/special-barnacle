def format_markdown_html(html_code: str):
    return html_code.replace("<ul>", '<ul class="ml-6 list-disc">')
