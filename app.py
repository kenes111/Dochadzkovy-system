import os
import io
from flask import Flask, request, jsonify, render_template, Response
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime
import enum
import csv

basedir = os.path.abspath(os.path.dirname(__file__))
app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(basedir, 'dochadzka.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)


# Udalosti
class TypZaznamu(enum.Enum):
    PRICHOD = "Príchod"
    ODCHOD = "Odchod"
    OBED_START = "Obed zaciatok"
    OBED_END = "Obed koniec"
    LEKAR_START = "Lekár - odchod"
    LEKAR_KONIEC = "Lekár - príchod"


# Databazy
class Zamestnanec(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    osobne_cislo = db.Column(db.String(10), unique=True, nullable=False)
    meno = db.Column(db.String(80), nullable=False)
    priezvisko = db.Column(db.String(80), nullable=False)
    aktivny = db.Column(db.Boolean, default=True)
    zaznamy = db.relationship('Zaznam', backref='zamestnanec', lazy=True)


class Prevadzka(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nazov = db.Column(db.String(120), nullable=False)
    kod_prevadzky = db.Column(db.String(10), unique=True, nullable=False)
    zaznamy = db.relationship('Zaznam', backref='prevadzka', lazy=True)


class Zaznam(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    casova_peciatka = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    typ_zaznamu = db.Column(db.Enum(TypZaznamu), nullable=False)
    zamestnanec_id = db.Column(db.Integer, db.ForeignKey('zamestnanec.id'), nullable=False)
    prevadzka_id = db.Column(db.Integer, db.ForeignKey('prevadzka.id'), nullable=False)


# TERMINAL API
@app.route('/api/zaznam', methods=['POST'])
def pridaj_zaznam():

    data = request.get_json()
    if not data or 'osobne_cislo' not in data or 'typ_zaznamu' not in data or 'kod_prevadzky' not in data:
        return jsonify({"status": "error", "message": "Chýbajúce dáta"}), 400

    zamestnanec = Zamestnanec.query.filter_by(osobne_cislo=data['osobne_cislo'], aktivny=True).first()
    prevadzka = Prevadzka.query.filter_by(kod_prevadzky=data['kod_prevadzky']).first()

    if not zamestnanec:
        return jsonify({"status": "error", "message": "Neznáme osobné číslo"}), 404
    if not prevadzka:
        return jsonify({"status": "error", "message": "Neznáma prevádzka"}), 404

    try:
        typ_enum = TypZaznamu[data['typ_zaznamu']]
    except KeyError:
        return jsonify({"status": "error", "message": "Neplatný typ záznamu"}), 400
    # Definicia posledneho zaznamu
    posledny_zaznam = Zaznam.query.filter_by(zamestnanec_id=zamestnanec.id).order_by(
        Zaznam.casova_peciatka.desc()).first()

    if typ_enum == TypZaznamu.ODCHOD:
        if not posledny_zaznam or posledny_zaznam.typ_zaznamu == TypZaznamu.ODCHOD:
            return jsonify({"status": "error",
                            "message": f"Chyba: Nie je možné zaznamenať odchod bez predchádzajúceho príchodu.(posledná akcia: {posledny_zaznam.typ_zaznamu.value})."}), 409  # 409 Conflict

    if typ_enum == TypZaznamu.PRICHOD:

        if posledny_zaznam and posledny_zaznam.typ_zaznamu != TypZaznamu.ODCHOD:
            return jsonify({"status": "error",
                            "message": f"Chyba: Zamestnanec je už v práci (posledná akcia: {posledny_zaznam.typ_zaznamu.value})."}), 410

    novy_zaznam = Zaznam(
        zamestnanec_id=zamestnanec.id,
        prevadzka_id=prevadzka.id,
        typ_zaznamu=typ_enum
    )
    db.session.add(novy_zaznam)
    db.session.commit()

    return jsonify({
        "status": "success",
        "message": f"Záznam '{typ_enum.value}' pre {zamestnanec.meno} {zamestnanec.priezvisko} bol uložený."
    }), 201


# Web Terminal
@app.route('/terminal/<kod_prevadzky>')
def terminal_view(kod_prevadzky):
    prevadzka = Prevadzka.query.filter_by(kod_prevadzky=kod_prevadzky).first_or_404()
    return render_template('terminal.html', prevadzka=prevadzka)


# Vytvorenie db + testovacie data
def setup_database(app):
    with app.app_context():
        db.create_all()
        # Vytvorenie testovacích dát, ak je databáza prázdna
        if not Zamestnanec.query.first():
            print("Vytváram testovacie dáta...")
            p1 = Prevadzka(nazov="Centrala Nové Mesto", kod_prevadzky="NM01")
            p2 = Prevadzka(nazov="Servis Trencin", kod_prevadzky="TN01")
            z1 = Zamestnanec(osobne_cislo="12345", meno="Jan", priezvisko="Novak")
            z2 = Zamestnanec(osobne_cislo="54321", meno="Maria", priezvisko="Vesela")
            z3 = Zamestnanec(osobne_cislo="11111", meno="Mario", priezvisko="Petrech")
            z4 = Zamestnanec(osobne_cislo="22222", meno="Gabriel", priezvisko="Gajdos")

            db.session.add_all([p1, p2, z1, z2, z3, z4])
            db.session.commit()


# web reporty
@app.route('/report')
def report_form():
    """Zobrazí stránku s formulárom na výber obdobia pre export."""
    return render_template('report.html')


# CSV vystup
@app.route('/export')
def export_csv():


    if request.args.get('predefined') == 'today':
        today_str = datetime.now().strftime('%Y-%m-%d')
        start_date_str = today_str
        end_date_str = today_str
    else:
        start_date_str = request.args.get('start_date')
        end_date_str = request.args.get('end_date')

    if not start_date_str or not end_date_str:
        return "Chyba: Musíte zadať začiatočný aj koncový dátum.", 400

    try:
        start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date()
        start_datetime = datetime.combine(start_date, datetime.min.time())

        end_date = datetime.strptime(end_date_str, '%Y-%m-%d').date()
        end_datetime = datetime.combine(end_date, datetime.max.time())
    except ValueError:
        return "Chyba: Nesprávny formát dátumu. Použite formát RRRR-MM-DD.", 400

    # Získame všetky záznamy za dané obdobie, zoradené podľa času
    zaznamy_query = Zaznam.query.join(Zamestnanec).join(Prevadzka) \
        .filter(Zaznam.casova_peciatka.between(start_datetime, end_datetime)) \
        .order_by(Zaznam.casova_peciatka.asc())

    vsetky_zaznamy = zaznamy_query.all()

    # Aktualny stav zamestnanca
    finalne_statusy = {}
    for zaznam in vsetky_zaznamy:
        # Pre každého zamestnanca si postupne prepisujeme jeho posledný záznam.
        # Keďže sú záznamy zoradené podľa času, na konci slučky zostane v slovníku ten posledný.
        finalne_statusy[zaznam.zamestnanec_id] = zaznam.typ_zaznamu

    # Definicia zmestnanec v praci
    statusy_v_praci = {TypZaznamu.PRICHOD, TypZaznamu.LEKAR_KONIEC, TypZaznamu.OBED_END}

    # Pridanie "zamestnanec v praci" do CSV
    output = io.StringIO()
    writer = csv.writer(output)


    writer.writerow(['ID Záznamu', 'Osobné číslo', 'Meno', 'Priezvisko', 'Prevádzka', 'Časová Pečiatka', 'Typ Záznamu',
                     'Aktuálny Stav'])


    for zaznam in vsetky_zaznamy:

        posledny_zaznam_typ = finalne_statusy.get(zaznam.zamestnanec_id)


        aktualny_stav = "V práci" if posledny_zaznam_typ in statusy_v_praci else "Mimo práce"

        writer.writerow([
            zaznam.id,
            zaznam.zamestnanec.osobne_cislo,
            zaznam.zamestnanec.meno,
            zaznam.zamestnanec.priezvisko,
            zaznam.prevadzka.nazov,
            zaznam.casova_peciatka.strftime('%Y-%m-%d %H:%M:%S'),
            zaznam.typ_zaznamu.value,
            aktualny_stav
        ])

    output.seek(0)

    return Response(
        output.getvalue().encode('utf-8'),
        mimetype='text/csv; charset=utf-8',
        headers={"Content-Disposition": f"attachment;filename=dochadzka_{start_date_str}-{end_date_str}.csv"}
    )


if __name__ == '__main__':
    setup_database(app)
    app.run(host='0.0.0.0', port=5000, debug=True)
