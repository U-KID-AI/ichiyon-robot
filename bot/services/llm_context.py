"""Bounded conversation facts, separate from the person's actual style examples."""
import json
import re


def format_reply(text):
    """Use chat line breaks, preserving quoted text and code verbatim."""
    output = []
    quoted = 0
    code = False
    for char in text.strip():
        if char == '`':
            code = not code
        if not code:
            if char in '「『':
                quoted += 1
            elif char in '」』':
                quoted = max(0, quoted - 1)
        output.append('\n' if char == '。' and not quoted and not code else char)
    return re.sub(r'\n{3,}', '\n\n', ''.join(output)).strip()


def response_mode(text):
    if re.search(r'お嬢様言葉|(?:口調|言葉)で(?:会話|話|喋)|ロールプレイ|なりきって', text):
        return 'style_play'
    explicit = r'詳しく|詳細に|具体的に|長めに|手順|比較して|仕組み|理由.{0,12}(?:説明|教えて)|[3３三]点'
    return 'explanation' if re.search(explicit, text) else 'ordinary'


def retrieval_context(history):
    # Generated lectures must not choose the next topic/style examples.
    return '\n'.join(row['text'] for row in history if not row['robot'])[-1200:]


def context_excerpt(text, limit):
    if len(text) <= limit:
        return text, False
    # This is a labelled context excerpt, never a shortened outgoing reply.
    marker = ' …[省略]… '
    if limit <= len(marker):
        return text[:limit], True
    half = (limit - len(marker)) // 2
    tail = limit - len(marker) - half
    return text[:half] + marker + text[-tail:], True


def prepare_context(history):
    bot_ids = {row['message_id'] for row in history if row['robot']}
    recent_bots = [row['message_id'] for row in history if row['robot']][-3:]
    rows = []
    human_budget = 4200
    for item in reversed(history):
        if item['message_id'] in bot_ids and item['message_id'] not in recent_bots:
            continue
        row = dict(item)
        limit = 160 if row['robot'] else min(600, human_budget)
        if limit <= 0:
            continue
        row['text'], row['excerpted'] = context_excerpt(row['text'], limit)
        row['source'] = 'generated_context_not_style' if row['robot'] else 'participant'
        if not row['robot']:
            human_budget -= len(row['text'])
        rows.append(row)
    return list(reversed(rows))


def make_prompt(latest, history, reference, examples):
    mode = response_mode(latest['text'])
    if reference:
        reference = dict(reference)
        reference['text'], reference['excerpted'] = context_excerpt(reference['text'], 240 if reference['robot'] else 600)
        reference['source'] = 'generated_context_not_style' if reference['robot'] else 'participant'
    return json.dumps({
        'reply_policy': {
            'mode_hint': mode,
            'current_task': ('無害な口調遊びの提案なので、依頼された口調をこの場で短く実演する。自分の柄ではないという拒否は依頼への返答にならない。恒久的な人格・経歴の変更ではない。'
                             if mode == 'style_play' else '相手の質問・提案・冗談の内容に応じる。相手の態度を批判することで返答を代用しない。'),
            'ordinary': '通常の雑談は一言や数十文字、概ね1〜2文で足りれば終える。文字数・文数の固定ではない。',
            'exception': '詳しい説明を求められた場合や必要な相談では、要点を満たすだけ十分に説明してよい。',
            'style_authority': '通常の口調は人格ガイドとperson_style_examplesが基準。口調遊びの明示依頼は今回だけの表現指定として扱う。generated_contextは話題・整合性の確認用で口調の模範ではない。',
        },
        'person_style_examples': examples,
        'conversation_context': prepare_context(history),
        'referenced_message': reference,
        'latest_message': latest,
        'respond_to': latest['message_id'],
    }, ensure_ascii=False)
