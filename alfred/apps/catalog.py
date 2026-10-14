"""The apps that procedural tasks are built from: each a few operations over a few kinds of records.

Everything here is written for Alfred. None of it comes from When2Call, BFCL or AgentDojo, and the topics those
benchmarks lean on (movies, weather, events, salons, travel, restaurants, music) are left out on purpose, so a
better score there means the judgment carried over, not that the same tools came back.

A request template is a sentence with {param} slots. A [bracketed] part is optional: it's said only when every slot
in it is, so dropping it leaves a request that's missing that detail. Slots outside brackets are always said.

Held-out apps (HELDOUT) are only ever used for evaluation.
"""
from __future__ import annotations

from dataclasses import dataclass, field

KINDS = ("string", "text", "date", "time", "integer", "amount", "boolean", "enum", "id")


@dataclass(frozen=True)
class Param:
    key: str
    kind: str
    names: tuple                # parameter-name variants, snake_case (camelCase is derived)
    desc: tuple                 # description variants
    label: str                  # how a person names it ("pickup date"), for questions and feedback
    q: tuple = ()               # how the assistant asks for it ("which city should I search in")
    values: tuple = ()          # values to draw from (enum: the options)
    say: dict = field(default_factory=dict)  # enum value -> words; boolean: {True: "with a drive-through"}
    fmt: str = "{v}"            # how a value reads in a request ("for {v} hours")
    default: object = None      # documented default, said in the description
    entity: str = ""            # kind "id": the entity whose records carry it


@dataclass(frozen=True)
class Entity:
    key: str
    id_fmt: str                 # "RX-{d5}": {dN} is N random digits, {AN} N random capitals
    fields: dict                # field -> values
    refs: tuple = ()            # how a person refers to one record ("my {drug} prescription")
    ref_field: str = ""         # the field the refs use; it's made unique among the records shown


@dataclass(frozen=True)
class Op:
    key: str
    writes: bool
    names: tuple
    desc: tuple
    label: str                  # what it does, as a verb phrase ("request a refill")
    req: tuple = ()
    opt: tuple = ()
    say: tuple = ()
    lists: str = ""             # the entity whose records it returns (reads)
    siblings: tuple = ()        # other ops here that look like they'd do this one's job


@dataclass(frozen=True)
class Domain:
    key: str
    app: str
    prefixes: tuple
    params: dict
    entities: dict
    ops: dict


def _domain(key, app, prefixes, params, entities, ops) -> Domain:
    return Domain(key, app, tuple(prefixes), {p.key: p for p in params}, {e.key: e for e in entities},
                  {o.key: o for o in ops})


CITIES = ("Boise", "Tacoma", "Ann Arbor", "Savannah", "Madison", "Duluth", "Fort Collins", "Burlington", "Eugene",
          "Bend", "Ithaca", "Missoula", "Santa Fe", "Asheville", "Chattanooga", "Spokane", "Flagstaff", "Billings")
FRIENDS = ("Jordan", "Priya", "Mateo", "Aisha", "Ken", "Lucia", "Omar", "Hannah", "Wei", "Tomas", "Ines", "Dev")
EMAILS = ("jordan.reyes@fastmail.example", "priya.n@postbox.example", "mateo.silva@inbox.example",
          "aisha.k@mailbox.example", "ken.watanabe@fastmail.example", "lucia.ferro@postbox.example",
          "omar.haddad@inbox.example", "hannah.b@mailbox.example")
STREETS = ("14 Alder Street", "220 Pine Avenue", "9 Harbor Road", "301 Main Street", "47 Birch Lane",
           "1180 River Drive", "62 College Avenue", "5 Mill Street")
TIMES = ("08:00", "09:30", "10:00", "11:15", "12:00", "13:30", "14:00", "15:45", "16:30", "18:00")


def city(names=("city", "location", "town"), q=("which city should I search in", "what city is this for")):
    return Param("city", "string", names, ("City to search in.", "The city or town.", "Where to look (city name)."),
                 "city", q, CITIES, fmt="in {v}")


def day(key, names, desc, label, q, default=None):
    return Param(key, "date", names, desc, label, q, default=default)


def email(key="email", names=("email", "recipient_email", "share_with"), label="email address",
          q=("what email address should I use", "who should I send it to (email address)")):
    return Param(key, "string", names, ("Email address of the person.", "Recipient's email address."), label, q,
                 EMAILS, fmt="{v}")


# ----------------------------------------------------------------------------- training apps

PHARMACY = _domain("pharmacy", "pharmacy", ("rx", "pharmacy", "meds"), [
    city(),
    day("open_on", ("open_on", "date", "open_date"), ("Only pharmacies open on this date (YYYY-MM-DD).",
        "Date the pharmacy must be open."), "date", ("which day do you need it open",), default="today"),
    Param("drive_through", "boolean", ("drive_through", "has_drive_thru"), ("Only pharmacies with a drive-through.",),
          "drive-through", say={True: "with a drive-through"}, default=False),
    Param("rx_id", "id", ("rx_id", "prescription_id", "rx_number"), ("The prescription number.",
          "ID of the prescription, from list_prescriptions."), "prescription", ("which prescription",),
          fmt="prescription {v}", entity="prescription"),
    day("pickup_date", ("pickup_date", "ready_by", "pickup_on"), ("Date to pick up the refill (YYYY-MM-DD).",
        "When the refill should be ready."), "pickup date", ("what day do you want to pick it up",
        "when should it be ready for pickup")),
    Param("to_pharmacy", "string", ("to_pharmacy", "destination_pharmacy"), ("Pharmacy to move it to.",),
          "pharmacy", ("which pharmacy should it go to",), ("Cedar Pharmacy", "Main Street Drug", "Lakeside Rx"),
          fmt="to {v}"),
    Param("status", "enum", ("status", "filter"), ("Which prescriptions to list.",), "status", (),
          ("active", "expired", "all"), say={"active": "active", "expired": "expired", "all": "all"}, default="active"),
], [
    Entity("prescription", "RX-{d5}", {"drug": ("lisinopril", "atorvastatin", "levothyroxine", "metformin",
           "sertraline", "amoxicillin", "albuterol inhaler"), "refills_left": ("0", "1", "2", "3")},
           ("my {drug} prescription", "the {drug}", "my {drug}"), "drug"),
    Entity("pharmacy", "PH-{d3}", {"name": ("Cedar Pharmacy", "Main Street Drug", "Lakeside Rx", "Northgate Pharmacy"),
           "address": STREETS, "hours": ("8 AM to 9 PM", "9 AM to 6 PM", "open 24 hours")}),
], [
    Op("find_pharmacy", False, ("find_pharmacies", "search_pharmacies", "pharmacy_search"),
       ("Find pharmacies in a city.", "Search for nearby pharmacies.", "Look up pharmacies by city."),
       "find a pharmacy", req=("city",), opt=("open_on", "drive_through"), lists="pharmacy",
       say=("Find a pharmacy[ {city}][ that's open {open_on}][ {drive_through}].",
            "Is there a pharmacy[ {city}][ {drive_through}] I could go to[ {open_on}]?",
            "I need to pick up meds[ {open_on}]. Can you find a pharmacy[ {city}]?")),
    Op("list_prescriptions", False, ("list_prescriptions", "get_prescriptions", "my_prescriptions"),
       ("List the user's prescriptions.", "Get the user's prescriptions with their numbers."), "list your prescriptions",
       opt=("status",), lists="prescription",
       say=("What prescriptions do I have[ that are {status}]?", "Show me my[ {status}] prescriptions.")),
    Op("request_refill", True, ("request_refill", "refill_prescription", "order_refill"),
       ("Request a refill of a prescription.", "Ask the pharmacy to refill a prescription."), "request a refill",
       req=("rx_id", "pickup_date"),
       say=("Refill {rx_id}[ for pickup {pickup_date}].", "Can you get {rx_id} refilled[ so I can pick it up "
            "{pickup_date}]?", "I'm almost out of {rx_id}. Request a refill[ for {pickup_date}].")),
    Op("transfer_prescription", True, ("transfer_prescription", "move_prescription"),
       ("Move a prescription to another pharmacy.",), "transfer a prescription to another pharmacy",
       req=("rx_id", "to_pharmacy"), siblings=("request_refill",),
       say=("Transfer {rx_id} {to_pharmacy}.", "Move {rx_id} over {to_pharmacy}, please.")),
])

FILES = _domain("files", "cloud drive", ("drive", "files", "docs"), [
    Param("query", "string", ("query", "search", "keywords"), ("Words to search for in file names.",
          "Search text."), "search words", ("what should I search for",), ("budget", "lease", "tax return",
          "resume", "itinerary", "recipes", "invoice")),
    Param("file_type", "enum", ("file_type", "type"), ("Only files of this type.",), "file type", (),
          ("any", "pdf", "document", "spreadsheet", "image"), say={"pdf": "PDF", "document": "document",
          "spreadsheet": "spreadsheet", "image": "image", "any": "any"}, fmt="{v}", default="any"),
    Param("file_id", "id", ("file_id", "id", "document_id"), ("ID of the file.", "The file's ID from search_files."),
          "file", ("which file",), fmt="file {v}", entity="file"),
    email(),
    Param("permission", "enum", ("permission", "access", "role"), ("What they can do with it.",), "access level",
          ("should they be able to edit it or only view it",), ("view", "comment", "edit"),
          say={"view": "view-only", "comment": "comment", "edit": "edit"}, fmt="with {v} access"),
    Param("folder", "string", ("folder", "destination", "target_folder"), ("Folder to move it into.",
          "Destination folder name."), "folder", ("which folder should it go in",), ("Taxes 2026", "Apartment",
          "Work", "Archive", "Family", "Receipts"), fmt="to {v}"),
], [
    Entity("file", "F-{d4}", {"name": ("2026 budget.xlsx", "Lease agreement.pdf", "Tax return 2025.pdf",
           "Resume draft.docx", "Lisbon itinerary.pdf", "Family recipes.docx", "Invoice 1043.pdf")},
           ("the {name} file", "{name}", "my {name}"), "name"),
], [
    Op("search_files", False, ("search_files", "find_files", "file_search"), ("Search the user's files by name.",
       "Find files in the drive."), "search your files", req=("query",), opt=("file_type",), lists="file",
       say=("Find my[ {file_type}] files about {query}.", "Search my drive for {query}[, {file_type} only].",
            "Where's that {query} file?")),
    Op("share_file", True, ("share_file", "share_document", "grant_access"), ("Share a file with someone.",
       "Give another person access to a file."), "share a file", req=("file_id", "email"), opt=("permission",),
       say=("Share {file_id}[ with {email}][ {permission}].", "Send {file_id}[ to {email}][ {permission}].",
            "Give[ {email}] access to {file_id}[ {permission}].")),
    Op("move_file", True, ("move_file", "file_move", "relocate_file"), ("Move a file into a folder.",),
       "move a file", req=("file_id", "folder"),
       say=("Move {file_id}[ {folder}].", "Put {file_id} in the right folder[ ({folder})].",
            "File {file_id} away[ {folder}].")),
    Op("delete_file", True, ("delete_file", "remove_file"), ("Delete a file.",), "delete a file",
       req=("file_id",), siblings=("move_file",),
       say=("Delete {file_id}.", "Get rid of {file_id} for good.")),
])

NOTES = _domain("notes", "notes", ("notes", "notebook"), [
    Param("title", "string", ("title", "name", "note_title"), ("Title of the note.",), "title",
          ("what should the note be called",), ("Groceries", "Packing list", "Gift ideas", "Book club",
          "Garden plan", "Questions for the landlord"), fmt="called {v}"),
    Param("body", "text", ("body", "content", "text"), ("What the note says.", "Text of the note."), "text",
          ("what should it say",), ("buy oat milk and eggs", "pack the charger and two sweaters",
          "ask about the heating bill", "plant tomatoes after the last frost", "a scarf for Mom"),
          fmt="saying {v}"),
    Param("query", "string", ("query", "search", "text"), ("Words to search for.",), "search words",
          ("what should I search for",), ("groceries", "the packing list", "gift ideas", "the garden", "the landlord", "book club")),
    Param("note_id", "id", ("note_id", "id"), ("ID of the note.",), "note", ("which note",), fmt="note {v}",
          entity="note"),
    Param("text", "text", ("text", "append", "line"), ("Text to add at the end.",), "text to add",
          ("what should I add",), ("bananas", "a rain jacket", "ask about parking", "a book for Dad"), fmt="{v}"),
], [
    Entity("note", "N-{d4}", {"title": ("Groceries", "Packing list", "Gift ideas", "Book club", "Garden plan",
           "Questions for the landlord")}, ("my {title} note", "the {title} note", "{title}"), "title"),
], [
    Op("create_note", True, ("create_note", "new_note", "add_note"), ("Create a new note.",), "create a note",
       req=("title", "body"),
       say=("Make a note[ {title}][ {body}].", "Start a new note[ {title}][ {body}].",
            "Can you jot down a note[ {title}][, {body}]?")),
    Op("search_notes", False, ("search_notes", "find_notes"), ("Search notes by text.",), "search your notes",
       req=("query",), lists="note", say=("Find my note about {query}.", "Do I have a note about {query}?")),
    Op("append_to_note", True, ("append_to_note", "add_to_note", "update_note"), ("Add text to the end of a note.",),
       "add to a note", req=("note_id", "text"),
       say=("Add {text} to {note_id}.", "Put {text} on {note_id}.", "Update {note_id}: add {text}.")),
    Op("delete_note", True, ("delete_note", "remove_note"), ("Delete a note.",), "delete a note",
       req=("note_id",), siblings=("append_to_note",), say=("Delete {note_id}.", "Throw away {note_id}.")),
])

EXPENSES = _domain("expenses", "shared expenses", ("split", "expenses", "splitter"), [
    Param("amount", "amount", ("amount", "total", "cost"), ("Amount in dollars.",), "amount",
          ("how much was it",), ("12.50", "18", "24.75", "40", "63.20", "85", "120"), fmt="${v}"),
    Param("description", "string", ("description", "what", "memo"), ("What it was for.",), "description",
          ("what was it for",), ("groceries", "pizza", "gas", "concert tickets", "cabin deposit", "utilities"),
          fmt="for {v}"),
    Param("group", "string", ("group", "group_name"), ("Name of the group.",), "group",
          ("which group is it in",), ("Roommates", "Ski trip", "Book club", "Cabin weekend"), fmt="in {v}"),
    Param("split", "enum", ("split", "split_type"), ("How to split it.",), "split", (), ("equal", "by_shares"),
          say={"equal": "evenly", "by_shares": "by shares"}, fmt="split {v}", default="equal"),
    Param("friend", "string", ("friend", "person", "from_user"), ("Who to ask.", "The friend's name."), "person",
          ("who should I request it from",), FRIENDS, fmt="{v}"),
    Param("note", "text", ("note", "message"), ("Note to include.",), "note", (), ("for the pizza", "gas money",
          "your half of the deposit"), fmt="with the note \"{v}\""),
], [
    Entity("balance", "B-{d3}", {"friend": FRIENDS, "owes": ("12.50", "30", "8.75", "41")}),
], [
    Op("add_expense", True, ("add_expense", "record_expense", "log_expense"), ("Record a shared expense.",),
       "add a shared expense", req=("amount", "description", "group"), opt=("split",),
       say=("Add[ {amount}] {description}[ {group}][, {split}].", "Log an expense[ {group}]: {description}[, "
            "{amount}][, {split}].", "I paid[ {amount}] {description}. Put it[ {group}][, {split}].")),
    Op("list_balances", False, ("list_balances", "get_balances", "who_owes"), ("Show who owes what.",),
       "show balances", opt=("group",), lists="balance",
       say=("Who owes me money[ {group}]?", "Show my balances[ {group}].")),
    Op("request_payment", True, ("request_payment", "request_money", "send_request"),
       ("Ask a friend to pay you.", "Send a payment request."), "request money from someone",
       req=("friend", "amount"), opt=("note",),
       say=("Request[ {amount}] from {friend}[ {note}].", "Ask {friend} to pay me[ {amount}][ {note}].")),
    Op("send_payment", True, ("send_payment", "pay_friend", "send_money"), ("Send money to a friend.",),
       "send money to someone", req=("friend", "amount"), siblings=("request_payment",),
       say=("Pay {friend} {amount}.", "Send {friend} {amount}.")),
])

ORDERS = _domain("orders", "online orders", ("orders", "shop", "store"), [
    day("since", ("since", "from_date", "after"), ("Only orders placed on or after this date.",), "date",
        ("since when",)),
    Param("order_id", "id", ("order_id", "id", "order_number"), ("The order number.",
          "Order number from list_orders."), "order", ("which order",), fmt="order {v}", entity="order"),
    Param("reason", "enum", ("reason", "return_reason"), ("Why it's being returned.",), "reason",
          ("why are you returning it",), ("damaged", "wrong_item", "no_longer_needed", "other"),
          say={"damaged": "because it arrived damaged", "wrong_item": "because they sent the wrong item",
               "no_longer_needed": "because I don't need it anymore", "other": "for another reason"}),
    Param("refund_method", "enum", ("refund_method", "refund_to"), ("Where the refund goes.",), "refund method",
          (), ("original_payment", "store_credit"), say={"original_payment": "to my card",
          "store_credit": "as store credit"}, fmt="refunded {v}", default="original_payment"),
], [
    Entity("order", "#{A1}{d5}", {"item": ("blue kettle", "desk lamp", "running shoes", "phone case", "yoga mat",
           "coffee grinder", "rain jacket"), "status": ("delivered", "in transit", "processing")},
           ("the {item}", "my {item} order", "the {item} I ordered"), "item"),
], [
    Op("list_orders", False, ("list_orders", "get_orders", "order_history"), ("List recent orders.",),
       "list your orders", opt=("since",), lists="order",
       say=("What have I ordered[ {since}]?", "Show my orders[ placed {since}].")),
    Op("track_package", False, ("track_package", "track_order", "shipment_status"),
       ("Where an order's package is.",), "track a package", req=("order_id",), lists="order",
       say=("Where's {order_id}?", "Track {order_id} for me.", "Has {order_id} shipped yet?")),
    Op("start_return", True, ("start_return", "return_item", "create_return"), ("Start a return for an order.",),
       "start a return", req=("order_id", "reason"), opt=("refund_method",),
       say=("Return {order_id}[ {reason}][, {refund_method}].", "Start a return for {order_id}[ {reason}][, "
            "{refund_method}].", "I want to send back {order_id}[ {reason}].")),
    Op("cancel_order", True, ("cancel_order", "order_cancel"), ("Cancel an order.",), "cancel an order",
       req=("order_id",), siblings=("start_return",),
       say=("Cancel {order_id}.", "Stop {order_id} before it ships.")),
])

CAR = _domain("car", "car care", ("garage", "car", "autocare"), [
    Param("vehicle_id", "id", ("vehicle_id", "car_id", "vehicle"), ("ID of the vehicle.",), "car",
          ("which car",), fmt="vehicle {v}", entity="vehicle"),
    Param("service", "enum", ("service", "service_type", "job"), ("The service needed.",), "service",
          ("what service does it need",), ("oil_change", "tire_rotation", "inspection", "brakes"),
          say={"oil_change": "an oil change", "tire_rotation": "a tire rotation", "inspection": "an inspection",
               "brakes": "a brake check"}, fmt="for {v}"),
    day("date", ("date", "appointment_date", "on"), ("Appointment date (YYYY-MM-DD).",), "date",
        ("what day works for you",)),
    Param("time", "time", ("time", "appointment_time", "at"), ("Appointment time (HH:MM, 24-hour).",), "time",
          ("what time",), TIMES),
    city(("city", "near", "location")),
], [
    Entity("vehicle", "V-{d3}", {"model": ("Civic", "Outback", "RAV4", "F-150", "Model 3", "Golf")},
           ("the {model}", "my {model}"), "model"),
    Entity("shop", "S-{d3}", {"name": ("Ridgeway Auto", "Quick Lube Plus", "Elm Street Garage", "Northside Tire"),
           "address": STREETS}),
], [
    Op("list_vehicles", False, ("list_vehicles", "my_vehicles", "get_cars"), ("List the user's vehicles.",),
       "list your cars", lists="vehicle", say=("Which cars do I have on file?", "List my vehicles.")),
    Op("find_shops", False, ("find_shops", "find_service_centers", "search_garages"), ("Find repair shops.",),
       "find a repair shop", req=("city",), opt=("service",), lists="shop",
       say=("Find a garage[ {city}][ {service}].", "Where can I take the car[ {service}][ {city}]?")),
    Op("book_service", True, ("book_service", "schedule_service", "book_appointment"),
       ("Book a service appointment.",), "book a service appointment", req=("vehicle_id", "service", "date"),
       opt=("time",), siblings=("find_shops",),
       say=("Book {vehicle_id}[ {service}][ {date}][ {time}].", "Schedule {vehicle_id} in[ {service}][ {date}]"
            "[ {time}].", "{vehicle_id} needs a service appointment[ {service}][ {date}][ {time}].")),
])

LIBRARY = _domain("library", "library", ("library", "catalog", "lib"), [
    Param("query", "string", ("query", "title_or_author", "search"), ("Title or author to look for.",),
          "title or author", ("what title or author",), ("Octavia Butler", "The Overstory", "Ursula K. Le Guin",
          "Pachinko", "Middlemarch", "Kazuo Ishiguro")),
    Param("format", "enum", ("format", "media_type"), ("Book, ebook or audiobook.",), "format", (),
          ("any", "book", "ebook", "audiobook"), say={"book": "print", "ebook": "ebook", "audiobook": "audiobook",
          "any": "any"}, fmt="as an {v}", default="any"),
    Param("item_id", "id", ("item_id", "id", "catalog_id"), ("ID of the catalog item.",), "item",
          ("which one",), fmt="item {v}", entity="item"),
    Param("branch", "string", ("branch", "pickup_branch", "location"), ("Branch to pick it up at.",),
          "pickup branch", ("which branch should it go to",), ("Central", "Eastside", "Riverside", "Hillcrest"),
          fmt="at the {v} branch"),
    Param("loan_id", "id", ("loan_id", "id", "checkout_id"), ("ID of the loan.",), "loan", ("which book",),
          fmt="loan {v}", entity="loan"),
], [
    Entity("item", "C-{d5}", {"title": ("Kindred", "The Overstory", "The Dispossessed", "Pachinko",
           "Middlemarch", "Klara and the Sun"), "format": ("book", "ebook", "audiobook")},
           ("{title}", "the {title} book", "a copy of {title}"), "title"),
    Entity("loan", "L-{d4}", {"title": ("Kindred", "The Overstory", "The Dispossessed", "Pachinko",
           "Middlemarch", "Klara and the Sun"), "due": ("in 2 days", "in 5 days", "tomorrow")},
           ("{title}", "my copy of {title}", "the {title} I have out"), "title"),
], [
    Op("search_catalog", False, ("search_catalog", "find_books", "catalog_search"), ("Search the catalog.",),
       "search the catalog", req=("query",), opt=("format",), lists="item",
       say=("Does the library have {query}[ {format}]?", "Look up {query} in the catalog[ {format}].")),
    Op("place_hold", True, ("place_hold", "reserve_item", "hold_item"), ("Put an item on hold.",), "place a hold",
       req=("item_id", "branch"),
       say=("Put {item_id} on hold[ {branch}].", "Reserve {item_id} for me[ {branch}].")),
    Op("list_loans", False, ("list_loans", "my_checkouts", "get_loans"), ("List what the user has checked out.",),
       "list your loans", lists="loan", say=("What do I have checked out?", "Show my library loans.")),
    Op("renew_loan", True, ("renew_loan", "renew_item", "extend_loan"), ("Renew a checked-out item.",),
       "renew a loan", req=("loan_id",), siblings=("place_hold",),
       say=("Renew {loan_id}.", "Can you renew {loan_id}? It's due soon.")),
])

PETS = _domain("pets", "pet care", ("pets", "petcare"), [
    Param("pet_id", "id", ("pet_id", "pet", "animal_id"), ("ID of the pet.",), "pet", ("which pet",),
          fmt="pet {v}", entity="pet"),
    city(("city", "near")),
    day("start_date", ("start_date", "from_date", "starts_on"), ("First day (YYYY-MM-DD).",), "start date",
        ("what day should it start",)),
    day("end_date", ("end_date", "to_date", "ends_on"), ("Last day (YYYY-MM-DD).",), "end date",
        ("what day should it end",)),
    Param("sitter_id", "id", ("sitter_id", "sitter"), ("ID of the sitter.",), "sitter", ("which sitter",),
          fmt="sitter {v}", entity="sitter"),
    Param("food", "string", ("food", "meal"), ("What it ate.",), "food", ("what did they eat",),
          ("dry kibble", "wet food", "chicken and rice", "a dental chew"), fmt="{v}"),
], [
    Entity("pet", "P-{d3}", {"name": ("Biscuit", "Miso", "Pepper", "Juniper", "Waffles", "Clover"),
           "species": ("dog", "cat", "rabbit")}, ("{name}", "my {species} {name}"), "name"),
    Entity("sitter", "PS-{d3}", {"name": ("Ana R.", "Marcus T.", "Leah W.", "Sam O."),
           "rate": ("$35/day", "$40/day", "$28/day")}, ("{name}", "the sitter {name}"), "name"),
], [
    Op("list_pets", False, ("list_pets", "my_pets"), ("List the user's pets.",), "list your pets", lists="pet",
       say=("Which pets do I have registered?", "List my pets.")),
    Op("find_sitters", False, ("find_sitters", "search_pet_sitters"), ("Find pet sitters near a city.",),
       "find a pet sitter", req=("city",), opt=("start_date",), lists="sitter",
       say=("Find a pet sitter[ {city}][ starting {start_date}].", "Who could watch my pets[ {city}]?")),
    Op("book_sitter", True, ("book_sitter", "book_pet_sitter", "hire_sitter"), ("Book a sitter for a pet.",),
       "book a pet sitter", req=("sitter_id", "pet_id", "start_date", "end_date"), siblings=("find_sitters",),
       say=("Book {sitter_id} to watch {pet_id}[ from {start_date}][ to {end_date}].",
            "Have {sitter_id} take care of {pet_id}[ from {start_date}][ until {end_date}].")),
    Op("log_feeding", True, ("log_feeding", "record_meal", "add_feeding"), ("Record a meal for a pet.",),
       "log a feeding", req=("pet_id", "food"),
       say=("Log that {pet_id} ate[ {food}].", "Record a meal for {pet_id}[: {food}].")),
])

UTILITIES = _domain("utilities", "home utilities", ("utility", "billing", "home"), [
    Param("account_id", "id", ("account_id", "account", "acct"), ("ID of the utility account.",), "account",
          ("which account",), fmt="account {v}", entity="account"),
    Param("enabled", "boolean", ("enabled", "on", "autopay"), ("Turn autopay on (true) or off (false).",),
          "on or off", ("should it be on or off",), say={True: "on", False: "off"}),
    Param("details", "text", ("details", "description", "notes"), ("What's happening.",), "details", (),
          ("the power has been out since this morning", "no water pressure upstairs", "internet keeps dropping"),
          fmt="({v})"),
], [
    Entity("account", "U-{d6}", {"service": ("electric", "water", "internet", "gas")},
           ("the {service} account", "my {service} bill", "the {service} bill"), "service"),
], [
    Op("list_accounts", False, ("list_accounts", "my_accounts", "get_accounts"), ("List the user's accounts.",),
       "list your utility accounts", lists="account", say=("Which utility accounts do I have?",)),
    Op("get_bill", False, ("get_bill", "latest_bill", "bill_details"), ("Get the latest bill for an account.",),
       "look up a bill", req=("account_id",), lists="account",
       say=("How much is {account_id}?", "What do I owe on {account_id}?")),
    Op("set_autopay", True, ("set_autopay", "toggle_autopay", "autopay"), ("Turn autopay on or off.",),
       "change autopay", req=("account_id", "enabled"),
       say=("Turn autopay {enabled} for {account_id}.", "Switch autopay {enabled} for {account_id}, please.")),
    Op("report_outage", True, ("report_outage", "outage_report"), ("Report an outage.",), "report an outage",
       req=("account_id",), opt=("details",),
       say=("Report an outage on {account_id}[ {details}].", "{account_id} is out. Report it[ {details}].")),
    Op("pay_bill", True, ("pay_bill", "make_payment"), ("Pay a bill.",), "pay a bill", req=("account_id",),
       siblings=("get_bill", "set_autopay"), say=("Pay {account_id}.", "Go ahead and pay {account_id} now.")),
])

FITNESS = _domain("fitness", "fitness tracker", ("fit", "fitness", "activity"), [
    Param("activity", "enum", ("activity", "workout_type", "sport"), ("Type of workout.",), "activity",
          ("what kind of workout",), ("run", "ride", "swim", "walk", "strength", "yoga"),
          say={"run": "a run", "ride": "a bike ride", "swim": "a swim", "walk": "a walk",
               "strength": "a strength workout", "yoga": "yoga"}),
    Param("minutes", "integer", ("minutes", "duration_minutes", "duration"), ("How long, in minutes.",),
          "duration", ("how long was it",), ("20", "30", "45", "60", "75"), fmt="{v} minutes"),
    day("date", ("date", "on", "day"), ("Date of the workout (YYYY-MM-DD).",), "date", ("what day was it",),
        default="today"),
    Param("weekly_minutes", "integer", ("weekly_minutes", "goal_minutes", "target"), ("Minutes per week.",),
          "weekly goal", ("how many minutes a week",), ("90", "120", "150", "200"), fmt="{v} minutes a week"),
    Param("workout_id", "id", ("workout_id", "id"), ("ID of the workout.",), "workout", ("which workout",),
          fmt="workout {v}", entity="workout"),
], [
    Entity("workout", "W-{d4}", {"activity": ("run", "ride", "swim", "walk", "yoga"),
           "minutes": ("25", "40", "55")}, ("the {activity} I logged", "my {activity}"), "activity"),
], [
    Op("log_workout", True, ("log_workout", "add_workout", "record_activity"), ("Log a workout.",),
       "log a workout", req=("activity", "minutes"), opt=("date",),
       say=("Log {activity}[, {minutes}][ {date}].", "I did {activity}[ {date}][ for {minutes}]. Add it.",
            "Record {activity}[ ({minutes})][ {date}].")),
    Op("get_summary", False, ("get_summary", "activity_summary", "weekly_stats"), ("Summarize workouts.",),
       "summarize your workouts", opt=("date",), lists="workout",
       say=("How much have I worked out[ since {date}]?", "Show my activity[ for {date}].")),
    Op("set_goal", True, ("set_goal", "set_weekly_goal", "update_goal"), ("Set a weekly goal.",),
       "set a weekly goal", req=("activity", "weekly_minutes"),
       say=("Set a goal of[ {weekly_minutes} of] {activity}.", "Make my weekly {activity} goal[ {weekly_minutes}].")),
    Op("delete_workout", True, ("delete_workout", "remove_workout"), ("Delete a logged workout.",),
       "delete a workout", req=("workout_id",), siblings=("log_workout",),
       say=("Delete {workout_id}.", "Remove {workout_id}, I logged it by mistake.")),
])

COURSES = _domain("courses", "school portal", ("school", "courses", "portal"), [
    Param("course_id", "id", ("course_id", "course", "class_id"), ("ID of the course.",), "course",
          ("which course",), fmt="course {v}", entity="course"),
    Param("status", "enum", ("status", "filter"), ("Which assignments.",), "status", (),
          ("upcoming", "overdue", "all"), say={"upcoming": "upcoming", "overdue": "overdue", "all": "all"},
          default="upcoming"),
    Param("assignment_id", "id", ("assignment_id", "assignment"), ("ID of the assignment.",), "assignment",
          ("which assignment",), fmt="assignment {v}", entity="assignment"),
    day("new_due", ("new_due_date", "extend_to", "new_deadline"), ("Requested new due date (YYYY-MM-DD).",),
        "new due date", ("what date do you want to ask for",)),
    Param("reason", "text", ("reason", "explanation", "note"), ("Why the extension is needed.",), "reason",
          ("what reason should I give",), ("I was sick this week", "I have three exams the same day",
          "a family emergency"), fmt="because {v}"),
], [
    Entity("course", "{A3}-{d3}", {"name": ("Organic Chemistry", "Statistics", "World History",
           "Intro to Psychology", "Linear Algebra")}, ("{name}", "my {name} class"), "name"),
    Entity("assignment", "A-{d4}", {"title": ("Lab report 3", "Problem set 5", "Essay draft", "Reading response",
           "Midterm project")}, ("{title}", "the {title}"), "title"),
], [
    Op("list_courses", False, ("list_courses", "my_courses", "get_classes"), ("List enrolled courses.",),
       "list your courses", lists="course", say=("What classes am I in?", "List my courses.")),
    Op("list_assignments", False, ("list_assignments", "get_assignments", "assignments"),
       ("List assignments for a course.",), "list assignments", req=("course_id",), opt=("status",),
       lists="assignment", say=("What's[ {status}] in {course_id}?", "Show me the[ {status}] assignments for "
                                "{course_id}.")),
    Op("request_extension", True, ("request_extension", "ask_extension", "extension_request"),
       ("Ask the instructor for an extension.",), "request an extension", req=("assignment_id", "new_due", "reason"),
       say=("Ask for an extension on {assignment_id}[ until {new_due}][ {reason}].",
            "Request more time for {assignment_id}[, until {new_due}][, {reason}].")),
    Op("submit_assignment", True, ("submit_assignment", "turn_in"), ("Submit an assignment.",),
       "submit an assignment", req=("assignment_id",), siblings=("request_extension",),
       say=("Submit {assignment_id}.", "Turn in {assignment_id} for me.")),
])

GROCERIES = _domain("groceries", "groceries", ("pantry", "grocery", "kitchen"), [
    Param("item", "string", ("item", "name", "product"), ("The item.",), "item", ("what should I add",),
          ("oat milk", "eggs", "basmati rice", "lemons", "coffee beans", "spinach", "olive oil")),
    Param("quantity", "integer", ("quantity", "qty", "count"), ("How many.",), "quantity", ("how many",),
          ("1", "2", "3", "6", "12"), fmt="{v}", default=1),
    Param("list_name", "string", ("list_name", "list"), ("Which list.",), "list", ("which list",),
          ("Groceries", "Costco run", "Party"), fmt="to the {v} list", default="Groceries"),
    Param("ingredient", "string", ("ingredient", "includes", "with"), ("An ingredient to use.",), "ingredient",
          ("what ingredient do you want to use",), ("chickpeas", "spinach", "salmon", "lentils", "zucchini"),
          fmt="with {v}"),
    Param("max_minutes", "integer", ("max_minutes", "time_limit"), ("Longest prep time.",), "time limit", (),
          ("20", "30", "45"), fmt="in under {v} minutes"),
    Param("store", "string", ("store", "shop"), ("Store to order from.",), "store", ("which store",),
          ("Fresh Market", "Corner Grocer", "Valley Foods"), fmt="from {v}"),
    day("delivery_date", ("delivery_date", "deliver_on"), ("Delivery date (YYYY-MM-DD).",), "delivery date",
        ("when should it arrive",)),
], [
    Entity("pantry_item", "I-{d3}", {"item": ("oat milk", "eggs", "basmati rice", "lemons", "coffee beans"),
           "on_hand": ("0", "1", "2", "half a bag")}),
    Entity("recipe", "R-{d3}", {"title": ("Chickpea curry", "Spinach frittata", "Lemon salmon", "Lentil soup",
           "Zucchini fritters"), "minutes": ("20", "25", "35", "40")}),
], [
    Op("add_to_list", True, ("add_to_list", "add_item", "list_add"), ("Add an item to a shopping list.",),
       "add something to a shopping list", req=("item",), opt=("quantity", "list_name"),
       say=("Add[ {quantity}] {item}[ {list_name}].", "Put {item} on the list[, {quantity} of them].",
            "We're out of {item}. Add it[ {list_name}].")),
    Op("check_pantry", False, ("check_pantry", "pantry_lookup", "in_stock"), ("Check what's in the pantry.",),
       "check the pantry", req=("item",), lists="pantry_item", say=("Do we have any {item}?", "How much {item} is "
                                                                    "left?")),
    Op("find_recipes", False, ("find_recipes", "recipe_search"), ("Find recipes.",), "find a recipe",
       req=("ingredient",), opt=("max_minutes",), lists="recipe",
       say=("Find a recipe[ {ingredient}][ {max_minutes}].", "What can I cook[ {ingredient}][ {max_minutes}]?")),
    Op("place_order", True, ("place_order", "order_groceries", "checkout"), ("Order groceries for delivery.",),
       "order groceries", req=("store", "delivery_date"), siblings=("add_to_list",),
       say=("Order the groceries {store} for delivery {delivery_date}.", "Place a grocery order {store}, "
            "delivered {delivery_date}.")),
])

# ----------------------------------------------------------------------------- held-out apps (evaluation only)

TUTORING = _domain("tutoring", "language lessons", ("lessons", "tutor", "lingo"), [
    Param("language", "enum", ("language", "lang", "subject"), ("Language to learn.",), "language",
          ("which language",), ("spanish", "japanese", "french", "arabic", "korean"),
          say={"spanish": "Spanish", "japanese": "Japanese", "french": "French", "arabic": "Arabic",
               "korean": "Korean"}, fmt="{v}"),
    Param("max_rate", "integer", ("max_rate", "max_price"), ("Highest hourly rate in dollars.",), "budget", (),
          ("25", "40", "60"), fmt="under ${v} an hour"),
    Param("tutor_id", "id", ("tutor_id", "teacher_id"), ("ID of the tutor.",), "tutor", ("which tutor",),
          fmt="tutor {v}", entity="tutor"),
    day("date", ("date", "lesson_date"), ("Lesson date (YYYY-MM-DD).",), "date", ("what day",)),
    Param("time", "time", ("time", "start_time"), ("Start time (HH:MM).",), "time", ("what time",), TIMES),
], [
    Entity("tutor", "T-{d3}", {"name": ("Rosa M.", "Kenji S.", "Amélie D.", "Yusuf A.", "Min-jun P."),
           "rate": ("$22/hr", "$35/hr", "$48/hr")}, ("{name}", "my tutor {name}"), "name"),
], [
    Op("find_tutors", False, ("find_tutors", "search_tutors"), ("Find tutors for a language.",), "find a tutor",
       req=("language",), opt=("max_rate",), lists="tutor",
       say=("Find me a[ {language}] tutor[ {max_rate}].", "Who teaches {language}[ {max_rate}]?")),
    Op("list_my_tutors", False, ("list_my_tutors", "my_tutors"), ("Tutors the user has booked before.",),
       "list your tutors", lists="tutor", say=("Who are my tutors?",)),
    Op("book_lesson", True, ("book_lesson", "schedule_lesson"), ("Book a lesson.",), "book a lesson",
       req=("tutor_id", "date", "time"),
       say=("Book a lesson with {tutor_id}[ {date}][ {time}].", "Set up a session with {tutor_id}[ {date}]"
            "[ {time}].")),
    Op("cancel_lesson", True, ("cancel_lesson",), ("Cancel a lesson.",), "cancel a lesson", req=("tutor_id",),
       siblings=("book_lesson",), say=("Cancel my lesson with {tutor_id}.",)),
])

REPAIRS = _domain("repairs", "home repair", ("homefix", "repairs", "pro"), [
    Param("trade", "enum", ("trade", "service", "category"), ("Kind of pro.",), "kind of pro",
          ("what kind of pro do you need",), ("plumber", "electrician", "handyman", "painter"),
          say={"plumber": "a plumber", "electrician": "an electrician", "handyman": "a handyman",
               "painter": "a painter"}),
    city(("city", "area")),
    Param("pro_id", "id", ("pro_id", "contractor_id"), ("ID of the contractor.",), "contractor",
          ("which contractor",), fmt="contractor {v}", entity="pro"),
    Param("job", "text", ("job", "job_description", "details"), ("What needs doing.",), "job", ("what's the job",),
          ("fix a leaking kitchen faucet", "replace two outlets", "patch and paint the hallway"), fmt="to {v}"),
    day("visit_date", ("visit_date", "date"), ("Date for the visit (YYYY-MM-DD).",), "visit date",
        ("what day should they come",)),
], [
    Entity("pro", "HP-{d3}", {"name": ("Brightline Electric", "Ortega Plumbing", "Handy Hands", "Fresh Coat Painting"),
           "rating": ("4.6", "4.8", "4.9")}, ("{name}", "the {name} people"), "name"),
], [
    Op("find_pros", False, ("find_pros", "search_contractors"), ("Find contractors.",), "find a contractor",
       req=("trade", "city"), lists="pro",
       say=("Find {trade}[ {city}].", "I need {trade}[ {city}]. Who's available?")),
    Op("saved_pros", False, ("saved_pros", "list_saved_contractors"), ("Contractors the user saved.",),
       "list saved contractors", lists="pro", say=("Which contractors have I saved?",)),
    Op("request_quote", True, ("request_quote", "get_estimate"), ("Ask a contractor for a quote.",),
       "request a quote", req=("pro_id", "job", "visit_date"),
       say=("Ask {pro_id} for a quote[ {job}][, visiting {visit_date}].", "Get an estimate from {pro_id}[ {job}]"
            "[; they can come {visit_date}].")),
    Op("leave_review", True, ("leave_review", "rate_contractor"), ("Review a contractor.",), "leave a review",
       req=("pro_id",), siblings=("request_quote",), say=("Leave {pro_id} a five-star review.",)),
])

BIKES = _domain("bikes", "bike share", ("bikeshare", "cycle", "bikes"), [
    Param("near", "string", ("near", "address", "location"), ("Address or place to search near.",), "location",
          ("where should I look",), STREETS, fmt="near {v}"),
    Param("station_id", "id", ("station_id", "dock_id"), ("ID of the station.",), "station", ("which station",),
          fmt="station {v}", entity="station"),
    Param("bike_type", "enum", ("bike_type", "type"), ("Classic or electric.",), "bike type",
          ("classic or electric",), ("classic", "electric"), say={"classic": "a classic bike",
          "electric": "an e-bike"}),
    Param("time", "time", ("time", "pickup_time"), ("Pickup time (HH:MM).",), "time", ("what time",), TIMES),
], [
    Entity("station", "ST-{d3}", {"name": ("Pine & 3rd", "Riverfront Park", "Union Station", "Library Plaza"),
           "bikes": ("3", "7", "12")}, ("the {name} station", "{name}"), "name"),
], [
    Op("find_stations", False, ("find_stations", "nearby_docks"), ("Find stations near a place.",),
       "find a station", req=("near",), lists="station", say=("Find a bike station {near}.",
                                                              "Any docks {near}?")),
    Op("reserve_bike", True, ("reserve_bike", "hold_bike"), ("Reserve a bike.",), "reserve a bike",
       req=("station_id", "bike_type", "time"),
       say=("Reserve {bike_type} at {station_id}[ {time}].", "Hold a bike for me at {station_id}[ ({bike_type})]"
            "[ {time}].")),
    Op("buy_pass", True, ("buy_pass", "purchase_pass"), ("Buy a membership pass.",), "buy a pass",
       req=("bike_type",), siblings=("reserve_bike",), say=("Buy me a monthly pass for {bike_type}.",)),
])

COWORK = _domain("cowork", "coworking", ("cowork", "desks", "space"), [
    Param("location_id", "id", ("location_id", "space_id"), ("ID of the location.",), "location",
          ("which location",), fmt="location {v}", entity="space"),
    day("date", ("date", "booking_date"), ("Date (YYYY-MM-DD).",), "date", ("what day",)),
    Param("desk_type", "enum", ("desk_type", "seat"), ("Kind of desk.",), "desk type", (),
          ("hot_desk", "dedicated", "phone_booth"), say={"hot_desk": "a hot desk", "dedicated": "a dedicated desk",
          "phone_booth": "a phone booth"}, default="hot_desk"),
    Param("people", "integer", ("people", "attendees", "capacity"), ("How many people.",), "number of people",
          ("how many people",), ("2", "4", "6", "8"), fmt="for {v} people"),
], [
    Entity("space", "CW-{d2}", {"name": ("Foundry", "Northlight", "The Annex", "Harbor Works"),
           "address": STREETS}, ("the {name} space", "{name}"), "name"),
], [
    Op("list_locations", False, ("list_locations", "spaces"), ("List coworking locations.",), "list locations",
       lists="space", say=("Which coworking spaces can I use?",)),
    Op("book_desk", True, ("book_desk", "reserve_desk"), ("Book a desk.",), "book a desk",
       req=("location_id", "date"), opt=("desk_type",),
       say=("Book {desk_type} at {location_id}[ {date}].", "Reserve me a spot at {location_id}[ {date}]"
            "[, {desk_type}].")),
    Op("find_rooms", False, ("find_rooms", "meeting_rooms"), ("Find meeting rooms.",), "find a meeting room",
       req=("location_id", "date", "people"), lists="space",
       say=("Is there a meeting room at {location_id}[ {date}][ {people}]?",)),
    Op("cancel_booking", True, ("cancel_booking",), ("Cancel a booking.",), "cancel a booking",
       req=("location_id",), siblings=("book_desk",), say=("Cancel my booking at {location_id}.",)),
])

TRAIN = {d.key: d for d in (PHARMACY, FILES, NOTES, EXPENSES, ORDERS, CAR, LIBRARY, PETS, UTILITIES, FITNESS,
                            COURSES, GROCERIES)}
HELDOUT = {d.key: d for d in (TUTORING, REPAIRS, BIKES, COWORK)}
