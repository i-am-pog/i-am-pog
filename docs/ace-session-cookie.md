# Getting the Ace session cookie

This is how the pipeline fetches your wholesale prices without anyone handling
your password. You log into Ace normally, in your own browser, and copy the
session it hands back.

**Do not paste the cookie into a chat, an issue, or this repo.** It goes into an
environment variable. Treat it like a password for as long as it lives.

## 1. Log in

Open <https://acegiftsplus.ca/account/login> in Chrome and log in as you
normally do. Then go to any page where you can **see your wholesale prices** —
that confirms the account is actually showing dealer pricing, which is the whole
point.

## 2. Copy the cookie

The reliable way is to copy the entire `cookie:` header, so you do not have to
work out which individual cookies matter.

1. Press **F12** (or right-click → Inspect) to open DevTools
2. Click the **Network** tab
3. **Reload the page** (Cmd/Ctrl + R) — the list fills up
4. Click the **first** row in the list (it will be the page itself, e.g. `products` or the page name)
5. Scroll down the right-hand panel to **Request Headers**
6. Find the line starting `cookie:` — right-click it → **Copy value**
   (or select the long text after `cookie:` and copy it)

It looks like this, and is usually long:

```
_shopify_y=1a2b...; _secure_session_id=9f8e...; secure_customer_sig=7d6c...; cart=...
```

The one that matters is **`secure_customer_sig`** — Shopify only sets it once a
customer is genuinely logged in. If it is missing, you copied it while logged
out. Copy the whole line anyway; extra cookies do no harm.

### Safari
Safari → Settings → Advanced → tick **Show features for web developers**, then
Develop → Show Web Inspector → Network, and follow the same steps.

### Firefox
F12 → Network → reload → click the first row → Request Headers → `Cookie`.

## 3. Give it to the pipeline

**Running on your own machine:** put it in `.env` beside the code:

```bash
ACE_SESSION_COOKIE='_shopify_y=1a2b...; secure_customer_sig=7d6c...'
```

Keep the single quotes — the value contains semicolons, which the shell would
otherwise read as the end of the command. `.env` is gitignored.

**Running in a hosted Claude Code session:** add it as an environment variable
in the environment's settings, not in chat. That way it never appears in a
transcript. See the
[Claude Code on the web docs](https://code.claude.com/docs/en/claude-code-on-the-web)
for where environment variables are configured.

That same environment also has to allow `acegiftsplus.ca` in its network
policy, or nothing in the session can reach the site at all.

## 4. Check it worked

```bash
python -m bw.cli auth-check ace
```

It first checks the cookie itself, with no network needed:

```
session:   cookie header, 7 values, 412 chars
session:   carries secure_customer_sig (logged in)
```

Then it samples a few products and shows the public price against what your
session sees:

```
  product                                     public    dealer   verdict
  lattafa-asad                                 64.99     26.00   WHOLESALE
```

**If both columns match**, the session is not doing anything — it expired, or
this store does not put dealer pricing in that endpoint. Do not carry on
regardless: those public prices would be treated as your cost and would poison
every price on the site. Use a price list instead:

```bash
python tools/parse_pricelist.py their-list.pdf -o data/ace_wholesale.csv
```

## When it stops working

Session cookies expire — days to weeks, depending on the store. Two things kill
it early:

- **logging out of Ace in that browser** (this is also how you revoke it
  deliberately, if you ever want to)
- Ace changing your account or their session settings

`auth-check` is the test. When it stops saying WHOLESALE, repeat steps 1–3.

A price list file has none of this upkeep, carries live quantities and barcodes,
and is the more accurate cost. Prefer it when you can get one.
