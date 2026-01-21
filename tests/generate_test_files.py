
import os
from ebooklib import epub
from docx import Document
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter
import shutil

# Data for scenarios
SCENARIOS = {
    "standard_fiction": {
        "title": "Alice in Wonderland (Excerpts)",
        "author": "Lewis Carroll",
        "file_base": "alice_test",
        "content": [
            ("Chapter 1: Down the Rabbit-Hole", "Alice was beginning to get very tired of sitting by her sister on the bank, and of having nothing to do: once or twice she had peeped into the book her sister was reading, but it had no pictures or conversations in it, 'and what is the use of a book,' thought Alice 'without pictures or conversation?'"),
            ("Chapter 2: The Pool of Tears", "Curiouser and curiouser! screamed Alice (she was so much surprised, that for the moment she quite forgot how to speak good English); 'now I'm opening out like the largest telescope that ever was! Good-bye, feet!' (for when she looked down at her feet, they seemed to be almost out of sight, they were getting so far off).")
        ]
    },
    "journal_devotional": {
        "title": "The Diary of a Nobody (Excerpts)",
        "author": "George Grossmith",
        "file_base": "diary_test",
        "content": [
            ("October 1", "To my mind the most significant entry in my diary is the fact that I have been promoted. A slight rise in salary, and a seat in the inner office."),
            ("October 2", "I find, on looking back at my diary, that I have omitted one or two little matters of interest. I have not mentioned that I have been three times late for the office.")
        ]
    },
    "devotional_utmost": {
        "title": "My Utmost for His Highest (Excerpts)",
        "author": "Oswald Chambers",
        "file_base": "utmost_test",
        "content": [
            ("January 1 - LET US KEEP TO THE POINT", "My Utmost for His Highest. 'My eager desire and hope being that I may never be ashamed.' Paul says - 'My determination is to be my utmost for His Highest.' To get there is a matter of will, not of debate."),
            ("January 2 - WILL YOU GO OUT WITHOUT KNOWING?", "He went out, not knowing whither he went. Hebrews 11:8. Have you been 'out' in this way? If so, there is no logical statement possible when anyone asks you what you are doing.")
        ]
    },
    "theological_biblical": {
        "title": "Theological Standards (Excerpts)",
        "author": "Westminster Divines",
        "file_base": "theological_test",
        "content": [
            ("Psalm 23", "The Lord is my shepherd; I shall not want. He maketh me to lie down in green pastures: he leadeth me beside the still waters."),
            ("Question 1", "What is the chief end of man? Answer: Man's chief end is to glorify God, and to enjoy him forever."),
            ("Sermon 5: On the Mount", "And seeing the multitudes, he went up into a mountain: and when he was set, his disciples came unto him. And he opened his mouth, and taught them.")
        ]
    },
    "complex_academic": {
        "title": "The Art of War (Excerpts)",
        "author": "Sun Tzu",
        "file_base": "art_of_war_test",
        "content": [
            ("I. Laying Plans", "Sun Tzu said: The art of war is of vital importance to the State. It is a matter of life and death, a road either to safety or to ruin. Hence it is a subject of inquiry which can on no account be neglected."),
            ("II. Waging War", "Sun Tzu said: In the operations of war, where there are in the field a thousand swift chariots, as many heavy chariots, and a hundred thousand mail-clad soldiers, with provisions enough to carry them a thousand li, the expenditure at home and at the front, including entertainment of guests, small items such as glue and paint, and sums spent on chariots and armor, will reach the total of a thousand ounces of silver per day. Such is the cost of raising an army of 100,000 men.")
        ]
    }
}

OUTPUT_DIR = "tests/data"
os.makedirs(OUTPUT_DIR, exist_ok=True)

def create_epub(key, data):
    book = epub.EpubBook()
    book.set_identifier(f'test_{key}')
    book.set_title(data['title'])
    book.set_language('en')
    book.add_author(data['author'])

    spine = ['nav']
    toc = []

    for i, (chap_title, chap_text) in enumerate(data['content']):
        c = epub.EpubHtml(title=chap_title, file_name=f'chap_{i+1}.xhtml', lang='en')
        c.content = f'<h1>{chap_title}</h1><p>{chap_text}</p>'
        book.add_item(c)
        spine.append(c)
        toc.append(epub.Link(f'chap_{i+1}.xhtml', chap_title, f'chap_{i+1}'))

    book.toc = tuple(toc)
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.spine = spine

    epub_path = os.path.join(OUTPUT_DIR, f"{data['file_base']}.epub")
    epub.write_epub(epub_path, book, {})
    print(f"Created EPUB: {epub_path}")
    return epub_path

def create_docx(key, data):
    doc = Document()
    doc.add_heading(data['title'], 0)
    doc.add_paragraph(f"By {data['author']}")

    for chap_title, chap_text in data['content']:
        doc.add_heading(chap_title, level=1)
        doc.add_paragraph(chap_text)

    docx_path = os.path.join(OUTPUT_DIR, f"{data['file_base']}.docx")
    doc.save(docx_path)
    print(f"Created DOCX: {docx_path}")
    return docx_path

def create_pdf(key, data):
    pdf_path = os.path.join(OUTPUT_DIR, f"{data['file_base']}.pdf")
    c = canvas.Canvas(pdf_path, pagesize=letter)
    width, height = letter

    c.setFont("Helvetica-Bold", 16)
    c.drawString(72, height - 72, data['title'])
    c.setFont("Helvetica", 12)
    c.drawString(72, height - 90, f"By {data['author']}")

    y_pos = height - 130
    
    for chap_title, chap_text in data['content']:
        if y_pos < 100:
            c.showPage()
            y_pos = height - 72
            
        c.setFont("Helvetica-Bold", 14)
        c.drawString(72, y_pos, chap_title)
        y_pos -= 20
        
        c.setFont("Helvetica", 12)
        # Simple text wrap logic for demo
        words = chap_text.split()
        line = ""
        for word in words:
            if c.stringWidth(line + " " + word) < 450:
                line += " " + word
            else:
                c.drawString(72, y_pos, line.strip())
                y_pos -= 14
                line = word
                if y_pos < 50:
                    c.showPage()
                    y_pos = height - 72
        
        if line:
            c.drawString(72, y_pos, line.strip())
            y_pos -= 30

    c.save()
    print(f"Created PDF: {pdf_path}")
    return pdf_path

def create_mobi_stub(key, data):
    # Just creating a placeholder file since we don't have conversion tools
    mobi_path = os.path.join(OUTPUT_DIR, f"{data['file_base']}.mobi")
    with open(mobi_path, 'wb') as f:
        f.write(b"MOBI_STUB_DATA") 
    print(f"Created MOBI (Stub): {mobi_path}")
    return mobi_path

if __name__ == "__main__":
    for key, data in SCENARIOS.items():
        print(f"\n--- Generating for {key} ---")
        create_epub(key, data)
        create_docx(key, data)
        try:
           create_pdf(key, data)
        except ImportError:
           print("Reportlab not found, skipping PDF")
        # create_mobi_stub(key, data) # Skip stub to avoid confusing the real app

