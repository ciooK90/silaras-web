import os, sqlite3, secrets
from datetime import datetime
from functools import wraps
from pathlib import Path
from flask import Flask, render_template, request, redirect, url_for, session, flash, send_from_directory, abort
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

BASE = Path(__file__).resolve().parent
DB_PATH = BASE / 'silaras.db'
UPLOAD_DIR = BASE / 'static' / 'uploads'
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
ALLOWED_EXTENSIONS = {'pdf','doc','docx','xls','xlsx','xlsm','csv','ods','odt','rtf','txt','ppt','pptx','jpg','jpeg','png','zip'}
app = Flask(__name__)
app.secret_key = os.environ.get('SILARAS_SECRET_KEY') or secrets.token_hex(32)
app.config['MAX_CONTENT_LENGTH'] = 20 * 1024 * 1024
app.config['UPLOAD_FOLDER'] = str(UPLOAD_DIR)

MENU = [
 ('Beranda','dashboard','⌂'), ('Informasi Penugasan','assignments','▣'),
 ('Data Dukung OPD','support','▰'), ('Dokumen Hasil Reviu','documents','▤'),
 ('Monitoring Dokumen','monitoring','▥'), ('Arsip LHR','archive','▣'), ('Panduan Penggunaan','guide','▤')
]

def db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA foreign_keys=ON')
    return con

def init_db():
    con = db()
    con.executescript('''
    CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL, full_name TEXT NOT NULL, role TEXT NOT NULL DEFAULT 'staff', created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS assignments(id INTEGER PRIMARY KEY AUTOINCREMENT, opd TEXT NOT NULL, review_type TEXT NOT NULL, fiscal_year INTEGER NOT NULL, team TEXT, start_date TEXT, end_date TEXT, status TEXT NOT NULL DEFAULT 'Berjalan', notes TEXT, created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS support_items(id INTEGER PRIMARY KEY AUTOINCREMENT, assignment_id INTEGER NOT NULL, name TEXT NOT NULL, description TEXT, status TEXT NOT NULL DEFAULT 'Belum diterima', due_date TEXT, received_at TEXT, notes TEXT, FOREIGN KEY(assignment_id) REFERENCES assignments(id) ON DELETE CASCADE);
    CREATE TABLE IF NOT EXISTS documents(id INTEGER PRIMARY KEY AUTOINCREMENT, assignment_id INTEGER NOT NULL, doc_type TEXT NOT NULL, title TEXT NOT NULL, filename TEXT NOT NULL, original_name TEXT NOT NULL, uploaded_by INTEGER, is_final INTEGER NOT NULL DEFAULT 0, notes TEXT, uploaded_at TEXT NOT NULL, FOREIGN KEY(assignment_id) REFERENCES assignments(id) ON DELETE CASCADE, FOREIGN KEY(uploaded_by) REFERENCES users(id));
    ''')
    # Migrasi aman untuk database lama: tambah kolom berkas jika belum ada.
    support_columns = {row['name'] for row in con.execute('PRAGMA table_info(support_items)').fetchall()}
    for column, definition in [
        ('filename', 'TEXT'),
        ('original_name', 'TEXT'),
        ('uploaded_by', 'INTEGER'),
        ('uploaded_at', 'TEXT'),
    ]:
        if column not in support_columns:
            con.execute(f'ALTER TABLE support_items ADD COLUMN {column} {definition}')

    if not con.execute('SELECT id FROM users LIMIT 1').fetchone():
        username = os.environ.get('SILARAS_ADMIN_USER','admin')
        password = os.environ.get('SILARAS_ADMIN_PASSWORD','GantiPassword123!')
        con.execute('INSERT INTO users(username,password_hash,full_name,role,created_at) VALUES(?,?,?,?,?)', (username, generate_password_hash(password), 'Administrator SILARAS', 'admin', now()))
    con.commit(); con.close()

def now(): return datetime.now().strftime('%Y-%m-%d %H:%M:%S')
def allowed(filename): return '.' in filename and filename.rsplit('.',1)[1].lower() in ALLOWED_EXTENSIONS

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('user_id'): return redirect(url_for('login', next=request.path))
        return f(*args, **kwargs)
    return decorated

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('user_id'): return redirect(url_for('login'))
        if session.get('role') != 'admin': abort(403)
        return f(*args, **kwargs)
    return decorated

@app.context_processor
def inject_globals(): return {'menu': MENU, 'active_user': session.get('full_name'), 'active_role': session.get('role')}

@app.route('/login', methods=['GET','POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username','').strip()
        password = request.form.get('password','')
        con=db(); user=con.execute('SELECT * FROM users WHERE username=?',(username,)).fetchone(); con.close()
        if user and check_password_hash(user['password_hash'], password):
            session.clear(); session['user_id']=user['id']; session['username']=user['username']; session['full_name']=user['full_name']; session['role']=user['role']
            return redirect(request.args.get('next') or url_for('dashboard'))
        flash('Nama pengguna atau kata sandi tidak sesuai.','error')
    return render_template('login.html')

@app.route('/logout', methods=['POST'])
@login_required
def logout(): session.clear(); return redirect(url_for('login'))

@app.route('/')
@login_required
def dashboard():
    con=db()
    stats={
      'assignments':con.execute('SELECT COUNT(*) FROM assignments').fetchone()[0],
      'support_total':con.execute('SELECT COUNT(*) FROM support_items').fetchone()[0],
      'support_done':con.execute("SELECT COUNT(*) FROM support_items WHERE status='Diterima'").fetchone()[0],
      'documents':con.execute('SELECT COUNT(*) FROM documents').fetchone()[0],
      'final_lhr':con.execute("SELECT COUNT(*) FROM documents WHERE doc_type='LHR' AND is_final=1").fetchone()[0],
    }
    recent=con.execute('SELECT d.*,a.opd FROM documents d JOIN assignments a ON a.id=d.assignment_id ORDER BY d.id DESC LIMIT 6').fetchall()
    assignments=con.execute('SELECT * FROM assignments ORDER BY id DESC LIMIT 5').fetchall(); con.close()
    return render_template('dashboard.html', stats=stats, recent=recent, assignments=assignments, title='Beranda')

@app.route('/assignments', methods=['GET','POST'])
@login_required
def assignments():
    con=db()
    if request.method=='POST':
        con.execute('INSERT INTO assignments(opd,review_type,fiscal_year,team,start_date,end_date,status,notes,created_at) VALUES(?,?,?,?,?,?,?,?,?)', (request.form['opd'].strip(),request.form['review_type'],int(request.form['fiscal_year']),request.form.get('team','').strip(),request.form.get('start_date') or None,request.form.get('end_date') or None,request.form.get('status','Berjalan'),request.form.get('notes','').strip(),now()))
        con.commit(); con.close(); flash('Penugasan berhasil ditambahkan.','success'); return redirect(url_for('assignments'))
    rows=con.execute('SELECT a.*, (SELECT COUNT(*) FROM support_items s WHERE s.assignment_id=a.id) support_count FROM assignments a ORDER BY id DESC').fetchall(); con.close()
    return render_template('assignments.html', rows=rows, title='Informasi Penugasan')

@app.route('/assignments/<int:item_id>/edit', methods=['GET','POST'])
@login_required
def edit_assignment(item_id):
    con=db(); row=con.execute('SELECT * FROM assignments WHERE id=?',(item_id,)).fetchone()
    if not row: con.close(); abort(404)
    if request.method=='POST':
        con.execute('UPDATE assignments SET opd=?,review_type=?,fiscal_year=?,team=?,start_date=?,end_date=?,status=?,notes=? WHERE id=?',(request.form['opd'].strip(),request.form['review_type'],int(request.form['fiscal_year']),request.form.get('team','').strip(),request.form.get('start_date') or None,request.form.get('end_date') or None,request.form.get('status','Berjalan'),request.form.get('notes','').strip(),item_id)); con.commit(); con.close(); flash('Penugasan diperbarui.','success'); return redirect(url_for('assignments'))
    con.close(); return render_template('edit_assignment.html', row=row, title='Edit Penugasan')

@app.route('/assignments/<int:item_id>/delete', methods=['POST'])
@admin_required
def delete_assignment(item_id):
    con=db(); files=con.execute('SELECT filename FROM documents WHERE assignment_id=?',(item_id,)).fetchall()
    con.execute('DELETE FROM assignments WHERE id=?',(item_id,)); con.commit(); con.close()
    for f in files:
        try: (UPLOAD_DIR / f['filename']).unlink(missing_ok=True)
        except OSError: pass
    flash('Penugasan dan data terkait dihapus.','success'); return redirect(url_for('assignments'))

@app.route('/support', methods=['GET','POST'])
@login_required
def support():
    con=db()
    if request.method=='POST':
        con.execute('INSERT INTO support_items(assignment_id,name,description,status,due_date,received_at,notes) VALUES(?,?,?,?,?,?,?)',(int(request.form['assignment_id']),request.form['name'].strip(),request.form.get('description','').strip(),request.form.get('status','Belum diterima'),request.form.get('due_date') or None,now() if request.form.get('status')=='Diterima' else None,request.form.get('notes','').strip())); con.commit(); con.close(); flash('Kebutuhan data dukung ditambahkan.','success'); return redirect(url_for('support'))
    rows=con.execute('SELECT s.*,a.opd FROM support_items s JOIN assignments a ON a.id=s.assignment_id ORDER BY s.id DESC').fetchall(); assignments_list=con.execute('SELECT id,opd FROM assignments ORDER BY opd').fetchall(); con.close()
    return render_template('support.html', rows=rows, assignments=assignments_list, title='Data Dukung OPD')


@app.route('/support/<int:item_id>/upload', methods=['POST'])
@login_required
def upload_support_file(item_id):
    # Normalisasi huruf besar/kecil agar peran admin bawaan ('admin') juga cocok.
    role = (session.get('role') or '').strip().upper()
    if role not in {'ADMIN', 'ANGGOTA TIM', 'ADMIN OPD'}:
        abort(403)

    file = request.files.get('file')
    if not file or not file.filename:
        flash('Pilih berkas yang akan diunggah.', 'error')
        return redirect(url_for('support'))
    if not allowed(file.filename):
        flash('Jenis berkas tidak diizinkan. Gunakan PDF, DOC/DOCX, XLS/XLSX, CSV, PPT/PPTX, gambar, atau ZIP.', 'error')
        return redirect(url_for('support'))

    con = db()
    item = con.execute('SELECT id FROM support_items WHERE id=?', (item_id,)).fetchone()
    if not item:
        con.close()
        abort(404)

    original = secure_filename(file.filename)
    stored = f"{secrets.token_hex(12)}_{original}"
    try:
        file.save(UPLOAD_DIR / stored)
        con.execute('UPDATE support_items SET filename=?, original_name=?, uploaded_by=?, uploaded_at=?, status=?, received_at=? WHERE id=?',
                    (stored, original, session.get('user_id'), now(), 'Diterima', now(), item_id))
        con.commit()
    except Exception:
        try:
            (UPLOAD_DIR / stored).unlink(missing_ok=True)
        except OSError:
            pass
        con.close()
        raise
    con.close()
    flash('Berkas data dukung berhasil diunggah.', 'success')
    return redirect(url_for('support'))

@app.route('/support/<int:item_id>/status', methods=['POST'])
@login_required
def support_status(item_id):
    status=request.form.get('status','Belum diterima'); received=now() if status=='Diterima' else None
    con=db(); con.execute('UPDATE support_items SET status=?,received_at=? WHERE id=?',(status,received,item_id)); con.commit(); con.close(); flash('Status data dukung diperbarui.','success'); return redirect(url_for('support'))

@app.route('/support/<int:item_id>/delete', methods=['POST'])
@admin_required
def delete_support(item_id):
    con=db(); con.execute('DELETE FROM support_items WHERE id=?',(item_id,)); con.commit(); con.close(); flash('Data dukung dihapus.','success'); return redirect(url_for('support'))

@app.route('/documents', methods=['GET','POST'])
@login_required
def documents():
    con=db()
    if request.method=='POST':
        file=request.files.get('file')
        if not file or not file.filename: flash('Pilih berkas yang akan diunggah.','error')
        elif not allowed(file.filename): flash('Jenis berkas tidak diizinkan. Gunakan PDF, DOC/DOCX, XLS/XLSX, JPG/PNG, atau ZIP.','error')
        else:
            original=secure_filename(file.filename); stored=f"{secrets.token_hex(12)}_{original}"; file.save(UPLOAD_DIR / stored)
            con.execute('INSERT INTO documents(assignment_id,doc_type,title,filename,original_name,uploaded_by,is_final,notes,uploaded_at) VALUES(?,?,?,?,?,?,?,?,?)',(int(request.form['assignment_id']),request.form['doc_type'],request.form['title'].strip(),stored,original,session['user_id'],1 if request.form.get('is_final')=='on' else 0,request.form.get('notes','').strip(),now())); con.commit(); con.close(); flash('Dokumen berhasil diunggah.','success'); return redirect(url_for('documents'))
    rows=con.execute('SELECT d.*,a.opd,u.full_name FROM documents d JOIN assignments a ON a.id=d.assignment_id LEFT JOIN users u ON u.id=d.uploaded_by ORDER BY d.id DESC').fetchall(); assignments_list=con.execute('SELECT id,opd FROM assignments ORDER BY opd').fetchall(); con.close()
    return render_template('documents.html', rows=rows, assignments=assignments_list, title='Dokumen Hasil Reviu')

@app.route('/documents/<int:item_id>/final', methods=['POST'])
@login_required
def final_document(item_id):
    con=db(); con.execute('UPDATE documents SET is_final=? WHERE id=?',(1 if request.form.get('is_final')=='1' else 0,item_id)); con.commit(); con.close(); flash('Status finalisasi dokumen diperbarui.','success'); return redirect(request.referrer or url_for('documents'))

@app.route('/documents/<int:item_id>/delete', methods=['POST'])
@admin_required
def delete_document(item_id):
    con=db(); row=con.execute('SELECT filename FROM documents WHERE id=?',(item_id,)).fetchone()
    if row:
        (UPLOAD_DIR / row['filename']).unlink(missing_ok=True); con.execute('DELETE FROM documents WHERE id=?',(item_id,)); con.commit()
    con.close(); flash('Dokumen dihapus.','success'); return redirect(url_for('documents'))

@app.route('/uploads/<path:filename>')
@login_required
def uploaded_file(filename):
    # Stored filenames are generated by the app; authentication is required for access.
    return send_from_directory(UPLOAD_DIR, filename, as_attachment=True)

@app.route('/monitoring')
@login_required
def monitoring():
    con=db(); assignments_list=con.execute('SELECT * FROM assignments ORDER BY id DESC').fetchall()
    data=[]
    for a in assignments_list:
        total=con.execute('SELECT COUNT(*) FROM support_items WHERE assignment_id=?',(a['id'],)).fetchone()[0]
        done=con.execute("SELECT COUNT(*) FROM support_items WHERE assignment_id=? AND status='Diterima'",(a['id'],)).fetchone()[0]
        docs=con.execute('SELECT COUNT(*) FROM documents WHERE assignment_id=?',(a['id'],)).fetchone()[0]
        lhr=con.execute("SELECT COUNT(*) FROM documents WHERE assignment_id=? AND doc_type='LHR' AND is_final=1",(a['id'],)).fetchone()[0]
        data.append({'assignment':a,'total':total,'done':done,'percent':round(done*100/total) if total else 0,'docs':docs,'lhr':lhr})
    con.close(); return render_template('monitoring.html', data=data, title='Monitoring Dokumen')

@app.route('/archive')
@login_required
def archive():
    q=request.args.get('q','').strip(); con=db()
    rows=con.execute("SELECT d.*,a.opd FROM documents d JOIN assignments a ON a.id=d.assignment_id WHERE d.doc_type='LHR' AND d.is_final=1 AND (a.opd LIKE ? OR d.title LIKE ? OR d.original_name LIKE ?) ORDER BY d.uploaded_at DESC",(f'%{q}%',f'%{q}%',f'%{q}%')).fetchall(); con.close()
    return render_template('archive.html', rows=rows, q=q, title='Arsip LHR')

@app.route('/guide')
@login_required
def guide(): return render_template('guide.html', title='Panduan Penggunaan')

@app.route('/users', methods=['GET','POST'])
@admin_required
def users():
    con=db()
    if request.method=='POST':
        username=request.form.get('username','').strip(); password=request.form.get('password',''); full_name=request.form.get('full_name','').strip(); role=request.form.get('role','staff')
        if len(password)<10: flash('Kata sandi minimal 10 karakter.','error')
        else:
            try: con.execute('INSERT INTO users(username,password_hash,full_name,role,created_at) VALUES(?,?,?,?,?)',(username,generate_password_hash(password),full_name,role,now())); con.commit(); flash('Pengguna dibuat.','success')
            except sqlite3.IntegrityError: flash('Nama pengguna sudah digunakan.','error')
        con.close(); return redirect(url_for('users'))
    rows=con.execute('SELECT id,username,full_name,role,created_at FROM users ORDER BY id').fetchall(); con.close(); return render_template('users.html', rows=rows, title='Kelola Pengguna')

@app.errorhandler(403)
def forbidden(e): return render_template('error.html', code=403, message='Anda tidak memiliki izin untuk membuka halaman ini.'),403
@app.errorhandler(404)
def not_found(e): return render_template('error.html', code=404, message='Halaman atau data yang dicari tidak ditemukan.'),404

init_db()
if __name__ == '__main__':
    app.run(debug=os.environ.get('FLASK_DEBUG')=='1', host='127.0.0.1', port=int(os.environ.get('PORT','5000')))
