#!/usr/bin/env python3

import os
import sys
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import threading
import time

THEME = {
    "primary": "#1abc9c",     
    "secondary": "#e67e22",   
    "background": "#2c3e50",  
    "text": "#ecf0f1",        
    "accent": "#9b59b6",      
    "error": "#e74c3c"        
}

def check_install_uncompyle6():
    try:
        import uncompyle6
        return True
    except ImportError:
        try:
            from tkinter import messagebox
            if messagebox.askyesno("Missing Dependency", 
                                 "The 'uncompyle6' package is required but not installed. Would you like to install it now?"):
                try:
                    import subprocess
                    
                    python_exe = sys.executable
                    install_cmd = [python_exe, "-m", "pip", "install", "uncompyle6"]
                    
                    install_window = tk.Toplevel()
                    install_window.title("Installing uncompyle6")
                    install_window.geometry("450x250")
                    install_window.configure(bg=THEME["background"])
                    
                    window_width = 450
                    window_height = 250
                    screen_width = install_window.winfo_screenwidth()
                    screen_height = install_window.winfo_screenheight()
                    center_x = int(screen_width/2 - window_width/2)
                    center_y = int(screen_height/2 - window_height/2)
                    install_window.geometry(f'{window_width}x{window_height}+{center_x}+{center_y}')
                    
                    style = ttk.Style()
                    style.configure("Install.TLabel", 
                                  background=THEME["background"],
                                  foreground=THEME["text"],
                                  font=("Segoe UI", 11))
                    
                    header_label = ttk.Label(install_window, 
                                          text="Installing uncompyle6", 
                                          background=THEME["background"],
                                          foreground=THEME["primary"],
                                          font=("Segoe UI", 14, "bold"))
                    header_label.pack(pady=(20, 5))
                    
                    install_label = ttk.Label(install_window, 
                                           text="This may take a few moments...",
                                           style="Install.TLabel")
                    install_label.pack(pady=5)
                    
                    progress_frame = ttk.Frame(install_window)
                    progress_frame.pack(fill=tk.X, padx=40, pady=15)
                    
                    style.configure("Installation.Horizontal.TProgressbar", 
                                  background=THEME["secondary"],
                                  troughcolor="#f0f0f0")
                    
                    progress = ttk.Progressbar(progress_frame, 
                                            style="Installation.Horizontal.TProgressbar",
                                            mode="indeterminate", 
                                            length=370)
                    progress.pack(fill=tk.X)
                    progress.start(10)
                    
                    status_var = tk.StringVar()
                    status_var.set("Running pip install...")
                    status_label = ttk.Label(install_window, 
                                          textvariable=status_var,
                                          background=THEME["background"],
                                          foreground=THEME["text"])
                    status_label.pack(pady=10)
                    
                    for alpha in range(0, 100, 10):
                        install_window.attributes('-alpha', alpha/100)
                        install_window.update()
                        time.sleep(0.02)
                    
                    install_window.update()
                    
                    process = subprocess.Popen(
                        install_cmd,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        universal_newlines=True
                    )
                    stdout, stderr = process.communicate()
                    
                    if process.returncode == 0:
                        progress.stop()
                        
                        progress.configure(mode="determinate", value=0)
                        for i in range(101):
                            progress.configure(value=i)
                            status_var.set(f"Installation successful! ({i}%)")
                            install_window.update()
                            time.sleep(0.01)
                        
                        status_var.set("Installation successful!")
                        
                        style.configure("Installation.Horizontal.TProgressbar", 
                                      background=THEME["secondary"])
                        
                        install_window.after(1000, install_window.destroy)
                        messagebox.showinfo("Success", "uncompyle6 installed successfully!")
                        return True
                    else:
                        progress.stop()
                        error_msg = f"Installation failed: {stderr}"
                        status_var.set("Installation failed!")
                        
                        style.configure("Installation.Horizontal.TProgressbar", 
                                      background=THEME["error"])
                        
                        messagebox.showerror("Installation Error", error_msg)
                        install_window.destroy()
                        
                        manual_instructions = """
To manually install uncompyle6, open a command prompt as administrator and run:

python -m pip install uncompyle6

Or if you have multiple Python versions:

C:\\Path\\to\\python.exe -m pip install uncompyle6
"""
                        messagebox.showinfo("Manual Installation", manual_instructions)
                        return False
                except Exception as e:
                    messagebox.showerror("Installation Error", f"Failed to install uncompyle6: {str(e)}")
                    return False
            else:
                messagebox.showerror("Missing Dependency", "Cannot continue without uncompyle6.")
                return False
        except Exception as e:
            print(f"Failed to show installation dialog: {str(e)}")
            return False

try:
    from pyc_to_py import pyc_to_py, process_directory
except ImportError:
    messagebox.showerror("Import Error", "Could not import from pyc_to_py.py. Make sure it's in the same directory.")
    sys.exit(1)

class PyctoPyGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("PYC Decompiler")
        self.root.geometry("900x750")
        self.root.resizable(True, True)
        self.root.configure(bg=THEME["background"])
        
        try:
            self.root.iconbitmap("python.ico")
        except:
            pass
        
        self.configure_styles()
        
        self.main_frame = ttk.Frame(root, padding="15")
        self.main_frame.pack(fill=tk.BOTH, expand=True)
        
        self.header_frame = ttk.Frame(self.main_frame)
        self.header_frame.pack(fill=tk.X, pady=(0, 15))
        
        self.header = ttk.Label(self.header_frame, text="PYC Decompiler", style="Header.TLabel")
        self.header.pack(pady=10)
        
        self.canvas = tk.Canvas(self.header_frame, height=3, bg=THEME["background"], 
                              highlightthickness=0)
        self.canvas.pack(fill=tk.X)
        
        self.setup_file_conversion()
        
        self.setup_log()
        
        self.status_var = tk.StringVar()
        self.status_var.set("Ready")
        self.status_bar = ttk.Label(self.main_frame, textvariable=self.status_var, 
                                  relief=tk.SUNKEN, anchor=tk.W, style="Status.TLabel")
        self.status_bar.pack(side=tk.BOTTOM, fill=tk.X)
        
        self.animate_underline()
        self.fade_in()

    def configure_styles(self):
        self.style = ttk.Style()
        
        self.style.configure("TFrame", background=THEME["background"])
        
        self.style.configure("TNotebook", background=THEME["background"])
        self.style.map("TNotebook", background=[("selected", THEME["background"])])
        
        self.style.configure("TNotebook.Tab", 
                           padding=[12, 5], 
                           background=THEME["background"],
                           foreground="#ffffff")
        
        self.style.map("TNotebook.Tab", 
                     background=[("selected", THEME["primary"]), ("!selected", THEME["background"])],
                     foreground=[("selected", "#ffffff"), ("!selected", "#ffffff")])
        
        self.root.option_add('*TNotebook.Tab*foreground', '#ffffff')
        
        self.style.configure("TButton", 
                           padding=8, 
                           font=("Segoe UI", 10),
                           background=THEME["primary"])
        self.root.option_add('*TButton*foreground', '#ffffff')
        
        self.style.map("TButton", 
                     background=[("active", THEME["accent"])],
                     relief=[("pressed", "sunken")])
        
        self.style.configure("Action.TButton", 
                           font=("Segoe UI", 10, "bold"),
                           background=THEME["secondary"])
        self.root.option_add('*Action.TButton*foreground', '#ffffff')
        
        self.style.map("Action.TButton", 
                     background=[("active", THEME["secondary"])])
        
        self.style.configure("TLabel", background=THEME["background"], 
                           foreground=THEME["text"], font=("Segoe UI", 10))
        self.style.configure("Header.TLabel", font=("Segoe UI", 18, "bold"), 
                           foreground=THEME["primary"], background=THEME["background"])
        
        self.style.configure("Status.TLabel", background=THEME["background"], padding=5, 
                           foreground=THEME["text"])
        
        self.style.configure("TEntry", fieldbackground="#1a2631", 
                           foreground="#ffffff", padding=5)
        
        self.style.configure("TCheckbutton", background=THEME["background"], 
                           foreground=THEME["text"], font=("Segoe UI", 10))
        
        self.style.configure("TLabelframe", background=THEME["background"], 
                           foreground=THEME["text"], font=("Segoe UI", 10, "bold"))
        self.style.configure("TLabelframe.Label", background=THEME["background"], 
                           foreground=THEME["primary"], font=("Segoe UI", 10, "bold"))
        
        self.style.configure("Horizontal.TProgressbar", 
                           background=THEME["secondary"],
                           troughcolor="#1a2631")

    def setup_file_conversion(self):
        file_frame = ttk.Frame(self.main_frame)
        file_frame.pack(fill=tk.X, padx=15, pady=15)
        
        ttk.Label(file_frame, text="PYC File:").grid(row=0, column=0, sticky=tk.W, padx=5, pady=5)
        self.file_path_var = tk.StringVar()
        file_entry = ttk.Entry(file_frame, textvariable=self.file_path_var, width=50, 
                             style="TEntry", foreground="#000000")
        file_entry.grid(row=0, column=1, padx=5, pady=5)
        
        browse_btn = ttk.Button(file_frame, text="Browse File", command=self.browse_file)
        browse_btn.grid(row=0, column=2, padx=5, pady=5)
        
        output_frame = ttk.Frame(self.main_frame)
        output_frame.pack(fill=tk.X, padx=15, pady=15)
        
        ttk.Label(output_frame, text="Output File:").grid(row=0, column=0, sticky=tk.W, padx=5, pady=5)
        self.output_path_var = tk.StringVar()
        output_entry = ttk.Entry(output_frame, textvariable=self.output_path_var, width=50,
                               style="TEntry", foreground="#000000")
        output_entry.grid(row=0, column=1, padx=5, pady=5)
        
        browse_out_btn = ttk.Button(output_frame, text="Browse Output", command=self.browse_output_file)
        browse_out_btn.grid(row=0, column=2, padx=5, pady=5)
        
        options_frame = ttk.LabelFrame(self.main_frame, text="Options")
        options_frame.pack(fill=tk.X, padx=15, pady=15)
        
        options_container = ttk.Frame(options_frame)
        options_container.pack(fill=tk.X, padx=10, pady=10)
        
        self.force_var = tk.BooleanVar(value=False)
        force_check = ttk.Checkbutton(options_container, text="Force overwrite existing files", 
                                    variable=self.force_var)
        force_check.grid(row=0, column=0, sticky=tk.W, padx=5, pady=5)
        
        self.pseudo_var = tk.BooleanVar(value=False)
        pseudo_check = ttk.Checkbutton(options_container, text="Generate pseudo-code file", 
                                     variable=self.pseudo_var)
        pseudo_check.grid(row=1, column=0, sticky=tk.W, padx=5, pady=5)
        
        pseudo_help = ttk.Label(options_container, 
                              text="Creates an additional file with detailed bytecode analysis",
                              font=("Segoe UI", 8), foreground=THEME["text"])
        pseudo_help.grid(row=1, column=1, sticky=tk.W, padx=5, pady=5)
        
        button_frame = ttk.Frame(self.main_frame)
        button_frame.pack(fill=tk.X, padx=15, pady=15)
        
        convert_btn = ttk.Button(button_frame, text="Convert File", command=self.convert_file, 
                               style="Action.TButton")
        convert_btn.pack(side=tk.RIGHT, padx=5, pady=5)

    def setup_log(self):
        log_frame = ttk.LabelFrame(self.main_frame, text="Log")
        log_frame.pack(fill=tk.BOTH, expand=True, padx=15, pady=15)
        
        scrollbar = ttk.Scrollbar(log_frame)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        
        self.log_text = tk.Text(log_frame, height=10, width=70, yscrollcommand=scrollbar.set,
                              bg="#1a2631", fg="#ffffff", font=("Consolas", 9),
                              insertbackground="#ffffff")
        self.log_text.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        self.log_text.config(state=tk.DISABLED)
        
        scrollbar.config(command=self.log_text.yview)
        
        btn_frame = ttk.Frame(log_frame)
        btn_frame.pack(side=tk.BOTTOM, fill=tk.X, padx=5, pady=5)
        
        clear_btn = ttk.Button(btn_frame, text="Clear Log", command=self.clear_log)
        clear_btn.pack(side=tk.RIGHT, padx=5, pady=5)

    def browse_file(self):
        filetypes = [("Python Compiled Files", "*.pyc"), ("All Files", "*.*")]
        filename = filedialog.askopenfilename(title="Select PYC File", filetypes=filetypes)
        if filename:
            self.file_path_var.set(filename)
            self.entry_animation(self.file_path_var, filename)
            
            output_path = ""
            if filename.endswith('.pyc'):
                output_path = filename[:-4] + '.py'
            else:
                output_path = filename + '.py'
            
            self.entry_animation(self.output_path_var, output_path)

    def entry_animation(self, string_var, target_text):
        string_var.set("")
        
        def fill_text(text, position=0):
            if position <= len(text):
                string_var.set(text[:position])
                self.root.after(5, lambda: fill_text(text, position + 1))
            
        fill_text(target_text)

    def browse_output_file(self):
        filetypes = [("Python Files", "*.py"), ("All Files", "*.*")]
        filename = filedialog.asksaveasfilename(title="Save PY File As", filetypes=filetypes, defaultextension=".py")
        if filename:
            self.entry_animation(self.output_path_var, filename)

    def log_message(self, message):
        self.log_text.config(state=tk.NORMAL)
        
        def type_message(msg, index=0):
            if index < len(msg):
                self.log_text.insert(tk.END, msg[index])
                self.log_text.see(tk.END)
                delay = 1 if msg[index] == '\n' else 5
                self.root.after(delay, lambda: type_message(msg, index + 1))
            else:
                self.log_text.insert(tk.END, "\n")
                self.log_text.see(tk.END)
                self.log_text.config(state=tk.DISABLED)
                
        type_message(message)

    def clear_log(self):
        original_bg = self.log_text["bg"]
        
        def fade_and_clear():
            self.log_text.config(state=tk.NORMAL)
            self.log_text.delete(1.0, tk.END)
            self.log_text.config(bg=THEME["background"])
            
            def fade_back(alpha=0):
                if alpha <= 1.0:
                    blend_color = self.blend_colors(THEME["background"], original_bg, alpha)
                    self.log_text.config(bg=blend_color)
                    self.root.after(20, lambda: fade_back(alpha + 0.1))
                else:
                    self.log_text.config(bg=original_bg)
                    self.log_text.config(state=tk.DISABLED)
                    
            self.root.after(100, lambda: fade_back())
        
        fade_and_clear()

    def blend_colors(self, color1, color2, ratio):
        return color2

    def update_status(self, message):
        original_bg = self.style.lookup("Status.TLabel", "background")
        
        self.style.configure("Status.TLabel", background=THEME["primary"], foreground="white")
        self.status_var.set(message)
        
        self.root.after(1000, lambda: self.style.configure("Status.TLabel", 
                                                       background=original_bg,
                                                       foreground=THEME["text"]))

    def convert_file(self):
        pyc_file = self.file_path_var.get().strip()
        output_file = self.output_path_var.get().strip()
        force = self.force_var.get()
        
        if not pyc_file:
            messagebox.showerror("Error", "Please select a PYC file")
            return
            
        try:
            import uncompyle6
        except ImportError:
            if not check_install_uncompyle6():
                return
        
        self.update_status("Converting file...")
        self.log_message(f"Converting file: {pyc_file}")
        
        threading.Thread(target=self._convert_file_thread, args=(pyc_file, output_file, force)).start()

    def _convert_file_thread(self, pyc_file, output_file, force):
        self.show_progress_animation(True)
        
        try:
            if not output_file:
                output_file = None
            
            create_pseudo = self.pseudo_var.get()
            
            success = pyc_to_py(pyc_file, output_file, force, use_alternative=True, create_pseudo=create_pseudo)
            
            if success:
                self.log_message(f"Successfully converted '{pyc_file}' to '{output_file}'")
                
                if create_pseudo:
                    pseudo_file = output_file[:-3] + '_pseudo.py' if output_file and output_file.endswith('.py') else output_file + '_pseudo.py'
                    self.log_message(f"Pseudo-code file created: '{pseudo_file}'")
                
                self.update_status("Conversion complete")
                self.show_conversion_success_animation()
            else:
                self.log_message(f"Failed to convert '{pyc_file}'")
                self.update_status("Conversion failed")
                self.show_conversion_error_animation()
        except Exception as e:
            self.log_message(f"Error: {str(e)}")
            self.update_status("Error during conversion")
            self.show_conversion_error_animation()
        finally:
            self.show_progress_animation(False)

    def show_progress_animation(self, show=True):
        if show:
            if not hasattr(self, 'progress_frame'):
                self.progress_frame = ttk.Frame(self.main_frame)
                self.progress_frame.pack(fill=tk.X, padx=15, pady=5, before=self.status_bar)
                
                self.progress = ttk.Progressbar(self.progress_frame, mode="indeterminate")
                self.progress.pack(fill=tk.X)
                self.progress.start(10)
        else:
            if hasattr(self, 'progress_frame'):
                self.progress.stop()
                self.progress_frame.destroy()
                delattr(self, 'progress_frame')

    def show_conversion_success_animation(self):
        success_canvas = tk.Canvas(self.main_frame, height=80, width=80, 
                                 bg=THEME["background"], highlightthickness=0)
        success_canvas.place(relx=0.5, rely=0.5, anchor=tk.CENTER)
        
        def draw_checkmark(scale):
            success_canvas.delete("all")
            if scale <= 1.0:
                x, y = 40, 40
                success_canvas.create_oval(
                    x-30*scale, y-30*scale, 
                    x+30*scale, y+30*scale, 
                    outline=THEME["secondary"], width=3*scale, fill="")
                
                if scale > 0.3:
                    adjusted_scale = (scale - 0.3) / 0.7
                    success_canvas.create_line(
                        x-15*adjusted_scale, y, 
                        x-5*adjusted_scale, y+15*adjusted_scale, 
                        width=3*scale, fill=THEME["secondary"], capstyle=tk.ROUND)
                    success_canvas.create_line(
                        x-5*adjusted_scale, y+15*adjusted_scale, 
                        x+20*adjusted_scale, y-15*adjusted_scale, 
                        width=3*scale, fill=THEME["secondary"], capstyle=tk.ROUND)
                
                self.root.after(30, lambda: draw_checkmark(scale + 0.05))
            else:
                def fade_out(alpha):
                    if alpha > 0:
                        success_canvas.update()
                        self.root.after(50, lambda: fade_out(alpha - 0.1))
                    else:
                        success_canvas.destroy()
                
                self.root.after(500, lambda: fade_out(1.0))
        
        draw_checkmark(0.1)

    def show_conversion_error_animation(self):
        error_canvas = tk.Canvas(self.main_frame, height=80, width=80, 
                               bg=THEME["background"], highlightthickness=0)
        error_canvas.place(relx=0.5, rely=0.5, anchor=tk.CENTER)
        
        def draw_xmark(scale):
            error_canvas.delete("all")
            if scale <= 1.0:
                x, y = 40, 40
                error_canvas.create_oval(
                    x-30*scale, y-30*scale, 
                    x+30*scale, y+30*scale, 
                    outline=THEME["error"], width=3*scale, fill="")
                
 
                if scale > 0.3:
                    adjusted_scale = (scale - 0.3) / 0.7 
                    error_canvas.create_line(
                        x-15*adjusted_scale, y-15*adjusted_scale, 
                        x+15*adjusted_scale, y+15*adjusted_scale, 
                        width=3*scale, fill=THEME["error"], capstyle=tk.ROUND)
                    error_canvas.create_line(
                        x+15*adjusted_scale, y-15*adjusted_scale, 
                        x-15*adjusted_scale, y+15*adjusted_scale, 
                        width=3*scale, fill=THEME["error"], capstyle=tk.ROUND)
                
                self.root.after(30, lambda: draw_xmark(scale + 0.05))
            else:
                def fade_out(alpha):
                    if alpha > 0:
                        error_canvas.update()
                        self.root.after(50, lambda: fade_out(alpha - 0.1))
                    else:
                        error_canvas.destroy()
                
                self.root.after(500, lambda: fade_out(1.0))
        
        draw_xmark(0.1)

    def tab_changed(self, event):
        tab = self.notebook.select()
        tab_id = self.notebook.index(tab)
        
        self.animate_underline(tab_id)
        
        tab_name = "Single File" if tab_id == 0 else "Directory"
        self.update_status(f"Switched to {tab_name} mode")

    def animate_underline(self, tab_id=None):
        self.canvas.delete("all")
        
        width = self.canvas.winfo_width()
        if width < 2:
            self.root.after(100, self.animate_underline)
            return
            
        line_color = THEME["primary"]
        line_width = 3
        
        line = self.canvas.create_line(
            width/2, line_width/2, 
            width/2, line_width/2, 
            fill=line_color, width=line_width, capstyle=tk.ROUND
        )
        
        def expand_line(current_width):
            if current_width < width:
                self.canvas.coords(
                    line, 
                    (width - current_width)/2, line_width/2,
                    (width + current_width)/2, line_width/2
                )
                self.root.after(5, lambda: expand_line(current_width + 20))
        
        expand_line(20)

    def fade_in(self):
        for alpha in range(0, 100, 5):
            self.root.attributes('-alpha', alpha/100)
            self.root.update()
            time.sleep(0.01)
        self.root.attributes('-alpha', 1.0)

def main():
    try:
        import uncompyle6
    except ImportError:
        if not check_install_uncompyle6():
            sys.exit(1)
    
    root = tk.Tk()
    app = PyctoPyGUI(root)
    root.mainloop()

if __name__ == "__main__":
    main()