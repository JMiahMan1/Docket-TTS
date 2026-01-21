
import os
from ebooklib import epub
from docx import Document
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter
import shutil

# Data for scenarios
SCENARIOS = {
    "standard_fiction": {
        "title": "Alice in Wonderland (Full Excerpts)",
        "author": "Lewis Carroll",
        "file_base": "alice_test",
        "content": [
            ("Chapter 1: Down the Rabbit-Hole", "Alice was beginning to get very tired of sitting by her sister on the bank, and of having nothing to do: once or twice she had peeped into the book her sister was reading, but it had no pictures or conversations in it, 'and what is the use of a book,' thought Alice 'without pictures or conversation?' So she was considering in her own mind (as well as she could, for the hot day made her feel very sleepy and stupid), whether the pleasure of making a daisy-chain would be worth the trouble of getting up and picking the daisies, when suddenly a White Rabbit with pink eyes ran close by her. There was nothing so VERY remarkable in that; nor did Alice think it so VERY much out of the way to hear the Rabbit say to itself, 'Oh dear! Oh dear! I shall be too late!' (when she thought it over afterwards, it occurred to her that she ought to have wondered at this, but at the time it all seemed quite natural); but when the Rabbit actually TOOK A WATCH OUT OF ITS WAISTCOAT-POCKET, and looked at it, and then hurried on, Alice started to her feet, for it flashed across her mind that she had never before seen a rabbit with either a waistcoat-pocket, or a watch to take out of it, and burning with curiosity, she ran across the field after it, and fortunately was just in time to see it pop down a large rabbit-hole under the hedge. In another moment down went Alice after it, never once considering how in the world she was to get out again."),
            ("Chapter 2: The Pool of Tears", "Curiouser and curiouser!' cried Alice (she was so much surprised, that for the moment she quite forgot how to speak good English); 'now I'm opening out like the largest telescope that ever was! Good-bye, feet!' (for when she looked down at her feet, they seemed to be almost out of sight, they were getting so far off). 'Oh, my poor little feet, I wonder who will put on your shoes and stockings for you now, dears? I am sure I shan't be able! I shall be a great deal too far off to trouble myself about you: you must manage the best way you can; --but I must be kind to them,' thought Alice, 'or perhaps they won't walk the way I want to go! Let me see: I'll give them a new pair of boots every Christmas.' And she went on planning to herself how she would manage it. 'They must go by the carrier,' she thought; 'and how funny it will seem, sending presents to one's own feet! And how odd the directions will look! ALICE'S RIGHT FOOT, ESQ. HEARTHRUG, NEAR THE FENDER, (WITH ALICE'S LOVE). Oh dear, what nonsense I'm talking!' Just then her head struck against the roof of the hall: in fact she was now more than nine feet high, and she at once took up the little golden key and hurried off to the garden door.")
        ]
    },
    "journal_devotional": {
        "title": "The Diary of a Nobody (Full Excerpts)",
        "author": "George Grossmith",
        "file_base": "diary_test",
        "content": [
            ("October 1", "To my mind the most significant entry in my diary is the fact that I have been promoted. A slight rise in salary, and a seat in the inner office. I have often wondered whether the Principal really knew of my existence; and now that I find he does, I feel passing rich. I shall capture the hearts of my colleagues, and be a general favourite. I have always been a favourite at home, and why not at the office? My dear wife Carrie appears to be even more pleased than I am. She said to me, 'Charles, you are a made man.' I replied, 'No, Carrie, I am a self-made man.' She laughed and said, 'You are a self-made goose.' We spent a very happy evening, and verified the statement that 'all work and no play makes Jack a dull boy' by going to the theatre in the evening to see 'The Corsican Brothers'. It was a splendid performance, although I was a bit annoyed by a person behind me who kept making loud remarks. However, I did not let it spoil my enjoyment of the evening. We returned home in high spirits, and I feel that this is the beginning of a new and prosperous era in my life."),
            ("October 2", "I find, on looking back at my diary, that I have omitted one or two little matters of interest. I have not mentioned that I have been three times late for the office. I must be more punctual. I have not mentioned that I have been to the theatre twice. I must not go so often. I have not mentioned that I have smoked six ounces of tobacco. I must smoke less. I have not mentioned that I have had a new suit. I must pay for it. I have not mentioned that I have had a row with the butcher. I must pay him. I have not mentioned that I have lost my umbrella. I must buy another. I have not mentioned that I have lost my temper. I must find it. I have not mentioned that I have been very happy. I must continue to be so. It is strange how we forget the little things that make up our daily lives. I resolve to be more diligent in recording these details in the future, as they may be of interest to me in years to come.")
        ]
    },
    "devotional_utmost": {
        "title": "My Utmost for His Highest (Full Excerpts)",
        "author": "Oswald Chambers",
        "file_base": "utmost_test",
        "content": [
            ("January 1 - LET US KEEP TO THE POINT", "My Utmost for His Highest. 'My eager desire and hope being that I may never be ashamed.' Paul says - 'My determination is to be my utmost for His Highest.' To get there is a matter of will, not of debate. It is a matter of surrendering the will to God, and allowing the Holy Spirit to work in us. We must not be sidetracked by secondary issues, but keep our eyes fixed on the goal. The goal is not our own happiness or success, but the glory of God. We must be willing to sacrifice everything for Him, even our own lives if necessary. This is not a call to a fanatical or unbalanced life, but to a life of deep devotion and service. It is a call to be our utmost for His Highest in every area of our lives, whether it be in our work, our relationships, or our spiritual walk. Let us then determine to be our utmost for His Highest, and never look back. The life that is fully surrendered to God is a life of power and purpose. It is a life that makes a difference in the world. So let us press on, with our eyes fixed on Jesus, the author and finisher of our faith."),
            ("January 2 - WILL YOU GO OUT WITHOUT KNOWING?", "He went out, not knowing whither he went. Hebrews 11:8. Have you been 'out' in this way? If so, there is no logical statement possible when anyone asks you what you are doing. One of the most difficult questions to answer in Christian work is 'What do you expect to do?' You don't know what you are going to do. The only thing you know is that God knows what He is doing. Continually examine your attitude toward God to see if you are willing to 'go out' in every area of your life, trusting in Him completely. It is not about knowing the destination, but about knowing the Guide. When we follow God, we may not see the path ahead clearly, but we can be sure that He is leading us in the right direction. So let us go out with joy and confidence, knowing that He is with us every step of the way. Faith is not believing that God will do what you want; it is believing that God will do what is right. It is trusting in His character and His promises, even when we don't understand His ways.")
        ]
    },
    "theological_biblical": {
        "title": "Theological Standards (Full Excerpts)",
        "author": "Westminster Divines",
        "file_base": "theological_test",
        "content": [
            ("Psalm 23", "The Lord is my shepherd; I shall not want. He maketh me to lie down in green pastures: he leadeth me beside the still waters. He restoreth my soul: he leadeth me in the paths of righteousness for his name's sake. Yea, though I walk through the valley of the shadow of death, I will fear no evil: for thou art with me; thy rod and thy staff they comfort me. Thou preparest a table before me in the presence of mine enemies: thou anointest my head with oil; my cup runneth over. Surely goodness and mercy shall follow me all the days of my life: and I will dwell in the house of the Lord for ever. This psalm, attributed to King David, is one of the most beloved passages in the Bible. It speaks of the intimate relationship between God and His people, using the metaphor of a shepherd and his sheep. Just as a shepherd cares for his flock, providing for their needs and protecting them from danger, so God cares for us. He guides us, comforts us, and assures us of His eternal presence. It is a psalm of deep trust and confidence in the goodness of God."),
            ("Question 1", "What is the chief end of man? Answer: Man's chief end is to glorify God, and to enjoy him forever. This first question of the Westminster Shorter Catechism sets the foundation for a reformed understanding of human purpose. It teaches that we exist not for our own self-gratification or worldly success, but to reflect the glory of our Creator. To glorify God involves acknowledging His sovereignty, obeying His commandments, and worshiping Him in spirit and in truth. But the answer doesn't stop there; it adds that we are to 'enjoy him forever.' This means that our relationship with God is not meant to be a dry, dutiful burden, but a source of profound joy and satisfaction. As John Piper has famously said, 'God is most glorified in us when we are most satisfied in Him.' This dual purpose of glorifying and enjoying God gives meaning and direction to every aspect of our lives."),
            ("Homily IV: On Patience", "Saint John Chrysostom. 'I speak not these things to shame you, but as my beloved sons I warn you.' 1 Corinthians 4:14. Paul does not say, 'I speak this to prove you,' nor 'to condemn you,' nor 'to confound you,' but 'to warn you.' And the naming of 'son' is a sufficient ground for forgiveness. For the father, though he be sharper than any other in his reproofs, desires not to punish, but to correct. So also he calls them 'beloved,' which represents the greatest love. For not simply 'sons,' but 'beloved' sons. For it is possible to have sons, and not to love them. But he joins both together. This Homily focuses on the virtue of patience in the face of correction. Chrysostom argues that true correction stems from love, akin to a father's love for his children."),
            ("Sermon 5: On the Mount", "And seeing the multitudes, he went up into a mountain: and when he was set, his disciples came unto him. And he opened his mouth, and taught them, saying, Blessed are the poor in spirit: for theirs is the kingdom of heaven. Blessed are they that mourn: for they shall be comforted. Blessed are the meek: for they shall inherit the earth. Blessed are they which do hunger and thirst after righteousness: for they shall be filled. Blessed are the merciful: for they shall obtain mercy. Blessed are the pure in heart: for they shall see God. Blessed are the peacemakers: for they shall be called the children of God. Blessed are they which are persecuted for righteousness' sake: for theirs is the kingdom of heaven. Blessed are ye, when men shall revile you, and persecute you, and shall say all manner of evil against you falsely, for my sake. Rejoice, and be exceeding glad: for great is your reward in heaven: for so persecuted they the prophets which were before you. This opening section of the Sermon on the Mount, known as the Beatitudes, describes the character and blessedness of the citizens of God's kingdom.")
        ]
    },
    "complex_corner_cases": {
        "title": "Comprehensive Edge Cases",
        "author": "QA Department",
        "file_base": "edge_cases_test",
        "content": [
            ("Table of Contents", "Chapter 1 ........ 5\nChapter 2 ........ 10\nThis section should be ideally removed or ignored by the chapterizer if it detects a TOC pattern, or treated as a prelude. Tests removal logic."),
            ("   Chapter 1   :   Whitespace Header   ", "This chapter header has significant leading and trailing whitespace. The regex '^[ \\t]*chapter...' should handle this correctly without needing strict start-of-line anchors that forbid indentation. The content here is standard text testing if the header is correctly identified despite the spaces. Repeated text for length: This chapter header has significant leading and trailing whitespace. The regex '^[ \\t]*chapter...' should handle this correctly without needing strict start-of-line anchors that forbid indentation. The content here is standard text testing if the header is correctly identified despite the spaces."),
            ("Part I: Structural Nesting", "This represents a 'Part' header. Often followed by Chapters. The system should identify 'Part I' as a valid split point. Testing Roman Numeral 'I'."),
            ("Chapter 2: Unicode & Symbols (Λόγος / שָׁלוֹם)", "This section contains non-ASCII characters to insure encoding is handled correctly in all formats (EPUB, DOCX, etc). Greek: Ἐν ἀρχῇ ἦν ὁ Λόγος. Hebrew: בְּרֵאשִׁית בָּרָא אֱלֹהִים אֵת הַשָּׁמַיִם וְאֵת הָאָרֶץ. Ensuring that the TTS pipeline (and file generation) doesn't choke on these symbols. The quick brown fox jumps over the lazy dog. 1234567890. Special symbols: @#$%^&*()."),
            ("Section 5.5: Decimal Headers", "Some layouts use decimals. 'Section 5.5' should be caught by the regex allowing digits. This checks if the regex accepts dots or just integers. If our regex is '([0-9]+|[IVX...])', it might miss 5.5. This is a good edge case test. If it fails, we know we need to update the regex to allow `[0-9]+(?:\\.[0-9]+)?`.")
        ]
    },
    "bible_references": {
        "title": "Bible Reference Variations",
        "author": "Test Suite",
        "file_base": "bible_test",
        "content": [
            ("ROM 9:28", "This tests the uppercase book abbreviation 'ROM' followed by chapter:verse. Content must be long enough. Romans 9:28 says, 'For he will finish the work, and cut it short in righteousness: because a short work will the Lord make upon the earth.' This text is used to verify that the chapterizer can handle standard Bible reference citations commonly found in theological works. Ideally, this should become its own chapter."),
            ("Rom. 9:28", "This tests the abbreviation with a dot 'Rom.' followed by chapter:verse. It distinguishes from 'Rom' without dot. The regex must handle the optional period. Content is filler text to ensure we meet the length requirements. We need to verify that the period does not break the parsing logic."),
            ("Is 43", "This tests the abbreviation 'Is' (Isaiah) without a dot. 'Is' is also a common English verb, so capitalization and context (start of line + number) are key. Isaiah 43 begins with 'But now thus saith the Lord that created thee, O Jacob...'. Testing if 'Is' followed by number is caught."),
            ("IS 43", "This tests uppercase 'IS' followed by number. This handles cases where the entire header is capitalized. Consistency in detection across case variations is important."),
            ("Is. 43", "This tests 'Is.' with a dot. This variation is common in older texts. The system should normalize or least detect this as a valid break point.")
        ]
    },
    "complex_academic": {
        "title": "The Art of War (Full Excerpts)",
        "author": "Sun Tzu",
        "file_base": "art_of_war_test",
        "content": [
            ("I. Laying Plans", "Sun Tzu said: The art of war is of vital importance to the State. It is a matter of life and death, a road either to safety or to ruin. Hence it is a subject of inquiry which can on no account be neglected. The art of war, then, is governed by five constant factors, to be taken into account in one's deliberations, when seeking to determine the conditions obtaining in the field. These are: (1) The Moral Law; (2) Heaven; (3) Earth; (4) The Commander; (5) Method and Discipline. The Moral Law causes the people to be in complete accord with their ruler, so that they will follow him regardless of their lives, undismayed by any danger. Heaven signifies night and day, cold and heat, times and seasons. Earth comprises distances, great and small; danger and security; open ground and narrow passes; the chances of life and death. The Commander stands for the virtues of wisdom, sincerely, benevolence, courage and strictness. By method and discipline are to be understood the marshaling of the army in its proper subdivisions, the graduations of rank among the officers, the maintenance of roads by which supplies may reach the army, and the control of military expenditure. These five heads should be familiar to every general: he who knows them will be victorious; he who knows them not will fail."),
            ("II. Waging War", "Sun Tzu said: In the operations of war, where there are in the field a thousand swift chariots, as many heavy chariots, and a hundred thousand mail-clad soldiers, with provisions enough to carry them a thousand li, the expenditure at home and at the front, including entertainment of guests, small items such as glue and paint, and sums spent on chariots and armor, will reach the total of a thousand ounces of silver per day. Such is the cost of raising an army of 100,000 men. When you engage in actual fighting, if victory is long in coming, then men's weapons will grow dull and their ardor will be damped. If you lay siege to a town, you will exhaust your strength. Again, if the campaign is protracted, the resources of the State will not be equal to the strain. Now, when your weapons are dulled, your ardor damped, your strength exhausted and your treasure spent, other chieftains will spring up to take advantage of your extremity. Then no man, however wise, will be able to avert the consequences that must ensue. Thus, though we have heard of stupid haste in war, cleverness has never been seen associated with long delays.")
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
        create_mobi_stub(key, data) 


