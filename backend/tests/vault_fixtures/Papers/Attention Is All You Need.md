---
title: "Attention Is All You Need"
source: "[[Attention Is All You Need.pdf]]"
doi: 10.48550/arXiv.1706.03762
authors:
  - Ashish Vaswani
  - Noam Shazeer
year: 2017
rating: 5
status: read
---
A transformer drops recurrence altogether.

> [!quote] [[Attention Is All You Need.pdf#page=3|p. 3]]
> We propose a new simple network architecture, the Transformer,
> based solely on attention mechanisms.
>
> The sequence models were all recurrent until this.

^a1b2c3

> [!QUOTE]- [[Attention Is All You Need.pdf#page=5|p. 5]]
> An attention function can be described as mapping a query
> and a set of key-value pairs to an output.

^d4e5f6

> [!quote]+ [p. 7](Attention%20Is%20All%20You%20Need.pdf#page=7)
> Multi-head attention allows the model
> to jointly attend

> [!quote] [[Attention Is All You Need.pdf#page=9|p. 9]]
> a quote
> ^not-mine

## Questions

- Why sinusoidal encodings?
  - > [!quote] [[Attention Is All You Need.pdf#page=6|p. 6]]
    > for any fixed offset k

    ^e7f8a9
  - Learned ones do as well, in my tests
- How does this compare with [[Conformer]]?
