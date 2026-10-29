"""Compare a transcript with a human reference using normalized character error rate."""
import argparse
import json
from pathlib import Path
import unicodedata


def normalize(text):
    return ''.join(c for c in unicodedata.normalize('NFKC', text).casefold()
                   if unicodedata.category(c)[0] in ('L', 'N'))


def character_errors(reference, hypothesis):
    previous = [(i, 0, 0, i) for i in range(len(hypothesis) + 1)]
    for row, expected in enumerate(reference, 1):
        current = [(row, 0, row, 0)]
        for column, actual in enumerate(hypothesis, 1):
            diagonal = previous[column - 1]
            substitution = (diagonal[0] + (expected != actual),
                            diagonal[1] + (expected != actual), diagonal[2], diagonal[3])
            above = previous[column]
            deletion = (above[0] + 1, above[1], above[2] + 1, above[3])
            left = current[column - 1]
            insertion = (left[0] + 1, left[1], left[2], left[3] + 1)
            current.append(min((substitution, deletion, insertion), key=lambda item: item[0]))
        previous = current
    edits, substitutions, deletions, insertions = previous[-1]
    return {'edits': edits, 'substitutions': substitutions, 'deletions': deletions,
            'insertions': insertions, 'cer': edits / len(reference)}


def score(path, reference):
    value = json.loads(Path(path).read_text(encoding='utf-8'))
    hypothesis = normalize(''.join(segment['text'] for segment in value['segments']))
    return {'path': str(path), 'hypothesisCharacters': len(hypothesis),
            **character_errors(reference, hypothesis)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reference', required=True, type=Path,
                        help='Human-checked text for exactly the same selected audio')
    parser.add_argument('--transcript', required=True, type=Path)
    parser.add_argument('--baseline', type=Path)
    args = parser.parse_args()
    reference = normalize(args.reference.read_text(encoding='utf-8'))
    if not reference:
        parser.error('the reference must contain letters or numbers')
    report = {'metric': 'character error rate', 'referenceCharacters': len(reference),
              'normalization': 'NFKC, casefold, remove punctuation and whitespace',
              'transcript': score(args.transcript, reference)}
    if args.baseline:
        report['baseline'] = score(args.baseline, reference)
        report['cerChange'] = report['transcript']['cer'] - report['baseline']['cer']
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
