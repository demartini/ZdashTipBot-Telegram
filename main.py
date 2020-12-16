from telegram.ext import Updater, CommandHandler
from bitcoinrpc.authproxy import AuthServiceProxy, JSONRPCException
from decimal import Decimal
import requests
import logging
import pymongo
import json

config = json.loads(open("config.json").read())

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)


def get_mongo():
    client = pymongo.MongoClient(config["mongo"]["host"], config["mongo"]["port"])
    return client[config["mongo"]["db"]]


def get_user_id(user):
    db = get_mongo()

    u = db.users.find_one({"username": user})

    return u


def get_user(user):
    db = get_mongo()

    u = db.users.find_one({"userid": user})

    if u is None:
        u = {"userid": user}
        db.users.insert(u)

    return u


def add_to_chat(user, chat):
    db = get_mongo()

    db.users.update({"userid": user["userid"]}, {"$addToSet": {"chats": chat}})


def is_registered(user):
    db = get_mongo()

    return db.users.count({"username": user}) != 0


def is_registered_id(user):
    db = get_mongo()

    return db.users.count({"userid": user}) != 0


def give_balance(user, amount):
    db = get_mongo()

    db.users.update({"userid": user["userid"]}, {"$inc": {"redeemed": float(-amount)}})

    return db.users.find_one({"userid": user["userid"]})


def get_balance(user):
    db = get_mongo()

    rpc = AuthServiceProxy(
        "http://%s:%s@%s:%d"
        % (
            config["rpc"]["user"],
            config["rpc"]["password"],
            config["rpc"]["host"],
            config["rpc"]["port"],
        )
    )
    address = get_address(user)

    received = rpc.getreceivedbyaddress(address)

    return received - Decimal(
        db.users.find_one({"userid": user["userid"]}).get("redeemed", 0)
    )


def get_unconfirmed(user):
    rpc = AuthServiceProxy(
        "http://%s:%s@%s:%d"
        % (
            config["rpc"]["user"],
            config["rpc"]["password"],
            config["rpc"]["host"],
            config["rpc"]["port"],
        )
    )
    address = get_address(user)

    received = rpc.getreceivedbyaddress(address)
    received_unconfirmed = rpc.getreceivedbyaddress(address, 0)

    return received_unconfirmed - received


def validate_address(address):
    rpc = AuthServiceProxy(
        "http://%s:%s@%s:%d"
        % (
            config["rpc"]["user"],
            config["rpc"]["password"],
            config["rpc"]["host"],
            config["rpc"]["port"],
        )
    )
    return rpc.validateaddress(address)["isvalid"]


def get_address(user):
    db = get_mongo()

    address = db.users.find_one({"userid": user["userid"]}).get("address", None)

    if address is None:
        rpc = AuthServiceProxy(
            "http://%s:%s@%s:%d"
            % (
                config["rpc"]["user"],
                config["rpc"]["password"],
                config["rpc"]["host"],
                config["rpc"]["port"],
            )
        )
        address = rpc.getnewaddress()
        db.users.update({"userid": user["userid"]}, {"$set": {"address": address}})

    return address


def start(bot, update):
    update.message.reply_text(
        "Hello! I'm a tipbot for the Hush crypto. "
        + "Add me to a group and start tipping!"
    )

    add_to_chat(get_user(update.message.from_user.id), update.message.chat_id)


def tip(bot, update):
    args = update.message.text.split()[1:]

    if len(args) == 2:
        # Unfortunatelly the only way i can currently think of for getting the
        # user ID for the username is if I get the user to register first.
        # Sucks, but I guess I need it to be done
        user = get_user_id(args[0])
        from_user = get_user(update.message.from_user.id)
        try:
            amount = Decimal(args[1])
        except decimal.InvalidOperation:
            update.message.reply_text("Usage: /tip <user> <amount>")
            return

        if user is not None:
            if amount > 0:
                if get_balance(from_user) - amount >= 0:
                    from_user = give_balance(from_user, -amount)
                    user = give_balance(user, amount)
                    bot.sendMessage(
                        chat_id=update.message.chat_id,
                        text="%s tipped %s %f Hush"
                        % (from_user["username"], args[0], amount),
                    )
                else:
                    update.message.reply_text("Not enough money!")
            else:
                update.message.reply_text("Invalid amount!")
        else:
            update.message.reply_text("%s is not registered!" % (args[0]))
    else:
        update.message.reply_text("Usage: /tip <user> <amount>")

    add_to_chat(get_user(update.message.from_user.id), update.message.chat_id)


def soak(bot, update):
    args = update.message.text.split()[1:]

    if len(args) == 1:
        from_user = get_user(update.message.from_user.id)
        try:
            amount = Decimal(args[0])
        except decimal.InvalidOperation:
            update.message.reply_text("Usage: /soak <amount>")
            return

        if amount > 0:
            if get_balance(from_user) - amount >= 0:
                db = get_mongo()

                users = db.users.find(
                    {
                        "chats": update.message.chat_id,
                        "userid": {"$ne": from_user["userid"]},
                        "username": {"$ne": None},
                    }
                )

                if users.count() > 0:
                    tip = amount / users.count()
                    from_user = give_balance(from_user, -amount)

                    usernames = []

                    for user in users:
                        print(user)
                        give_balance(user, tip)
                        usernames.append(user["username"])

                    users_str = ", ".join(usernames)

                    print(users.count())

                    for user in users:
                        print(user)
                        give_balance(user, tip)

                    bot.sendMessage(
                        chat_id=update.message.chat_id,
                        text="%s soaked %f Hush to %s!"
                        % (from_user["username"], tip, users_str),
                    )
                else:
                    update.message.reply_text(
                        "No users on this channel have" " interacted with the bot."
                    )
            else:
                update.message.reply_text("Not enough money!")
        else:
            update.message.reply_text("Invalid amount")
    else:
        update.message.reply_text("Usage: /soak <amount>")

    add_to_chat(get_user(update.message.from_user.id), update.message.chat_id)


def balance(bot, update):
    bal = get_balance(get_user(update.message.from_user.id))

    r = requests.get("https://api.cryptonator.com/api/ticker/%s-%s" % ("ok", "usd"))
    usd = Decimal(bal) * Decimal(r.json()["ticker"]["price"])

    unconfirmed = ""

    if get_unconfirmed(get_user(update.message.from_user.id)) > 0:
        unconfirmed = "(+ %s unconfirmed)" % get_unconfirmed(
            get_user(update.message.from_user.id)
        )

    update.message.reply_text("You have %s Hush (%f USD) %s" % (bal, usd, unconfirmed))

    add_to_chat(get_user(update.message.from_user.id), update.message.chat_id)


def register(bot, update):
    args = update.message.text.split()[1:]

    if len(args) == 1:
        username = args[0]

        if not is_registered_id(update.message.from_user.id):
            if not is_registered(username):
                db = get_mongo()

                db.users.insert_one(
                    {"username": username, "userid": update.message.from_user.id}
                )
                update.message.reply_text("You're now registered as %s!" % args[0])
            else:
                update.message.reply_text("Username is already in use!")
        else:
            db = get_mongo()

            db.users.update(
                {"userid": update.message.from_user.id},
                {"$set": {"username": username}},
            )
            update.message.reply_text("Your nick was updated to %s!" % args[0])
    else:
        update.message.reply_text("Usage: /register <username>")

    add_to_chat(get_user(update.message.from_user.id), update.message.chat_id)


def deposit(bot, update):
    update.message.reply_text(
        "Your deposit address is %s"
        % get_address(get_user(update.message.from_user.id))
    )

    add_to_chat(get_user(update.message.from_user.id), update.message.chat_id)


def withdraw(bot, update):
    bal = get_balance(get_user(update.message.from_user.id))

    args = update.message.text.split()[1:]

    if len(args) == 2:
        try:
            amount = Decimal(args[1])
        except decimal.InvalidOperation:
            update.message.reply_text("Usage: /withdraw <address> <amount>")
            return
        if bal - amount >= 0 and amount > 1:
            if validate_address(args[0]):
                rpc = AuthServiceProxy(
                    "http://%s:%s@%s:%d"
                    % (
                        config["rpc"]["user"],
                        config["rpc"]["password"],
                        config["rpc"]["host"],
                        config["rpc"]["port"],
                    )
                )
                rpc.settxfee(0.5)
                txid = rpc.sendtoaddress(args[0], amount - 1)
                give_balance(get_user(update.message.from_user.id), -amount)
                update.message.reply_text(
                    "Withdrew %f Hush! TX: %s"
                    % (amount - 1, "https://www.zpool.ca/explorer/HUSH?txid=" + txid)
                )
            else:
                update.message.reply_text("Invalid address")
        else:
            update.message.reply_text(
                "amount has to be more than 1, and " + "you need to have enough money"
            )
    else:
        update.message.reply_text("Usage: /withdraw <address> <amount>")

    add_to_chat(get_user(update.message.from_user.id), update.message.chat_id)


def convert(bot, update):
    args = update.message.text.split()[1:]

    if len(args) == 3:
        try:
            amount = Decimal(args[0])
        except decimal.InvalidOperation:
            update.message.reply_text("Usage: /convert <amount> <from> <to>")
            return

        request = requests.get(
            "https://api.cryptonator.com/api/ticker/%s-%s" % (args[1], args[2])
        )

        ticker = request.json()

        if ticker["success"]:
            res = Decimal(ticker["ticker"]["price"]) * amount
            base = ticker["ticker"]["base"]
            target = ticker["ticker"]["target"]
            update.message.reply_text("%f %s = %f %s" % (amount, base, res, target))
        else:
            update.message.reply_text("Error: %s " % ticker["error"])

    else:
        update.message.reply_text("Usage: /convert <amount> <from> <to>")

    add_to_chat(get_user(update.message.from_user.id), update.message.chat_id)


def market(bot, update):
    args = update.message.text.split()[1:]

    if len(args) > 0:
        base = args[0]
        if len(args) > 1:
            target = args[1]
        else:
            target = "usd"
    else:
        base = "ok"
        target = "btc"

    request = requests.get(
        "https://api.cryptonator.com/api/full/%s-%s" % (base, target)
    )

    ticker = request.json()

    if ticker["success"]:
        price = Decimal(ticker["ticker"]["price"])
        volume = ticker["ticker"]["volume"]
        change = ticker["ticker"]["change"]
        markets = ticker["ticker"]["markets"]

        message = "Price: %s Volume: %s Change: %s\n" % (price, volume, change)

        for market in markets:
            name = market["market"]
            price = market["price"]
            volume = market["volume"]
            message += "\n%s - Price: %s Volume: %s" % (name, price, volume)

        update.message.reply_text(message)
    else:
        update.message.reply_text("Error: %s " % ticker["error"])

    add_to_chat(get_user(update.message.from_user.id), update.message.chat_id)


if __name__ == "__main__":
    updater = Updater(config["token"])

    updater.dispatcher.add_handler(CommandHandler("start", start))
    updater.dispatcher.add_handler(CommandHandler("tip", tip))
    updater.dispatcher.add_handler(CommandHandler("register", register))
    updater.dispatcher.add_handler(CommandHandler("balance", balance))
    updater.dispatcher.add_handler(CommandHandler("bal", balance))
    updater.dispatcher.add_handler(CommandHandler("deposit", deposit))
    updater.dispatcher.add_handler(CommandHandler("withdraw", withdraw))
    updater.dispatcher.add_handler(CommandHandler("convert", convert))
    updater.dispatcher.add_handler(CommandHandler("con", convert))
    updater.dispatcher.add_handler(CommandHandler("market", market))
    updater.dispatcher.add_handler(CommandHandler("soak", soak))

    updater.start_polling()
    updater.idle()
