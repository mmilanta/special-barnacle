import mistune
def markdown_to_html(text: str) -> str:
    mkd = mistune.html(text)
    return mkd.replace("<ul>", '<ul class="ml-6 list-disc">')
