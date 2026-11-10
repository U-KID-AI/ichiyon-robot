"""Small retrieval hints, not a replacement for the model's contextual judgement."""
import re

NEGATIVE = re.compile(r'^(?:いや[、，\s]|知ら[なね]|興味ない|どうでも|勝手に|急に何|自分で考え|無理|無駄)|(?:お前|自分).{0,25}(?:べき|悪い|責任)|(?:すべき|するべき|であるべき)')

def situation(text, context=''):
    if re.search(r'真面目|真剣|相談|落ち込|悩ん|つらい|辛い|助けて', text):
        return 'serious'
    playful = r'冗談|無茶振り|ボケ|ツッコミ|ごっこ|必殺技.*考え|ラスボス.*名前|世界の支配者|給食大臣|光合成.*生き|という設定|になる設定|ってことに|[wｗ]{2,}'
    if re.search(playful, text) or re.search(r'[wｗ笑][!！。\s]*$', text) or (re.match(r'じゃあ|その|そいつ|それで', text) and re.search(playful, context)):
        return 'playful'
    return 'open'

def diverse(examples, limit, avoid=()):
    """Keep relevant rank order but avoid a whole prompt of one rejection pattern."""
    selected, deferred, starts, bodies = [], [], set(), set()
    negative = False
    for row in examples:
        body = row['reply'].strip()
        if not body or body in bodies or body in avoid:
            continue
        bodies.add(body)
        start = re.sub(r'[\s、。！？!?]', '', body)[:4]
        neg = bool(NEGATIVE.search(body))
        if start in starts or (negative and neg):
            deferred.append(row)
            continue
        selected.append(row)
        starts.add(start)
        negative |= neg
    return (selected + deferred)[:limit]
