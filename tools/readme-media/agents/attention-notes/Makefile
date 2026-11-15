# make: the figure, then the notes (aux files in build/)
notes.pdf: notes.tex sections/*.tex figures/attention-map.pdf
	latexmk -pdf -synctex=1 -interaction=nonstopmode -halt-on-error -auxdir=build notes.tex

figures/attention-map.pdf: figures/attention-map.tex
	latexmk -pdf -synctex=1 -interaction=nonstopmode -halt-on-error -auxdir=build/figures -outdir=figures figures/attention-map.tex
