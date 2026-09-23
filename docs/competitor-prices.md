# Getting a competitor's prices to Claude

Two ways. The first needs no settings changed by anyone.

## 1. Save the file in your own browser

Shopify stores publish their catalogue as a plain file. Open this in Chrome:

    https://perfumeonline.ca/products.json?limit=250

You will see a wall of text. That is correct — it is the data, not a mistake.

Press **Ctrl+S** (**Cmd+S** on a Mac) and save it. Then upload the file here.

That page holds 250 products. For more, change the page number at the end:

    https://perfumeonline.ca/products.json?limit=250&page=2
    https://perfumeonline.ca/products.json?limit=250&page=3

Keep going until a page comes back nearly empty — that means you reached the
end. Upload whichever files you get; more is better, but even one page is
enough to check whether our prices are in the right range.

Then:

```bash
python -m bw.cli reprice ace --market perfumeonline --market-file saved.json
```

The same works for any Shopify store: fragrancebuy.ca, fragrance365.ca, and
acegiftsplus.ca for Ace's own product photos.

## 2. Let this session reach the site directly

Better if you plan to re-check prices regularly, because then it runs on a
schedule instead of someone saving files.

The environment's network policy currently denies these hosts. Open the cloud
environment menu in the session title bar, choose **Edit**, and under **Network
access** either pick a broader access level or add the domains to the allowed
list:

    perfumeonline.ca
    fragrancebuy.ca
    acegiftsplus.ca

Access levels are explained at
<https://code.claude.com/docs/en/claude-code-on-the-web>.

After that, no files need saving:

```bash
python -m bw.cli reprice ace --market perfumeonline
```

## What cannot happen

Claude runs in a container in the cloud, not on your computer. It cannot open,
see or drive your browser, and it cannot reach a site your environment blocks.
Anything that needs your browser needs you at the keyboard — which is why
option 1 exists.
