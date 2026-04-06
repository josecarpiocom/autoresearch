#!/bin/bash
# Reset jokes-prompting example to initial state
cat > prompt.txt << 'EOF'
Escribe 10 chistes cortos en español sobre Pedro Sánchez y Trump en 2025-2026.

Cada chiste debe ser autocontenido, menos de 100 palabras, y hacer gracia a cualquier adulto que haya visto las noticias.

Separa cada chiste con una línea que contenga solo `---`.
EOF
rm -f autoresearch-results.tsv judge_feedback.txt generator_output.txt
echo "Reset complete. Run: python ../../run.py --baseline"
