-- Table layout of a FitDesk v1.2.0 database (structure only, no data). Used by the tests.
CREATE TABLE app_settings(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            setting_key TEXT UNIQUE NOT NULL,
            setting_value TEXT,
            updated_at TEXT
        , gst_number TEXT, gst_percent TEXT, terms_conditions TEXT, gym_name TEXT, tagline TEXT, address TEXT, phone TEXT, email TEXT, logo_path TEXT, signature_path TEXT, brand_color TEXT, whatsapp_number TEXT, instagram_url TEXT, facebook_url TEXT, whatsapp_template TEXT, email_template TEXT, thank_you_message TEXT);
CREATE TABLE audit_log(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            username TEXT,
            role TEXT,
            action TEXT NOT NULL,
            module TEXT NOT NULL,
            description TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY(user_id) REFERENCES users(id)
        );
CREATE TABLE communication_history(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            member_id INTEGER,
            transaction_id INTEGER,
            communication_type TEXT NOT NULL,
            recipient TEXT,
            receipt_no TEXT,
            subject TEXT,
            message TEXT,
            status TEXT NOT NULL DEFAULT 'Prepared',
            sent_at TEXT,
            created_at TEXT,
            FOREIGN KEY(member_id) REFERENCES members(id),
            FOREIGN KEY(transaction_id) REFERENCES membership_transactions(id)
        );
CREATE TABLE diet_foods(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    serving_qty REAL,
                    serving_unit TEXT,
                    kcal REAL,
                    protein_g REAL,
                    carbs_g REAL,
                    fat_g REAL,
                    allergens TEXT,
                    source TEXT NOT NULL DEFAULT 'manual',
                    source_note TEXT,
                    is_active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT,
                    updated_at TEXT
                );
CREATE TABLE diet_meal_items(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    meal_id INTEGER NOT NULL,
                    food_id INTEGER,
                    food_name TEXT NOT NULL,
                    portion TEXT,
                    quantity REAL,
                    unit TEXT,
                    allergens TEXT,
                    kcal REAL,
                    protein_g REAL,
                    carbs_g REAL,
                    fat_g REAL,
                    notes TEXT,
                    sort_order INTEGER NOT NULL DEFAULT 0,
                    FOREIGN KEY(meal_id) REFERENCES diet_meals(id),
                    FOREIGN KEY(food_id) REFERENCES diet_foods(id)
                );
CREATE TABLE diet_meals(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    template_id INTEGER,
                    plan_version_id INTEGER,
                    name TEXT NOT NULL,
                    meal_time TEXT,
                    sort_order INTEGER NOT NULL DEFAULT 0,
                    notes TEXT,
                    CHECK ((template_id IS NULL) <> (plan_version_id IS NULL)),
                    FOREIGN KEY(template_id) REFERENCES diet_templates(id),
                    FOREIGN KEY(plan_version_id) REFERENCES diet_plan_versions(id)
                );
CREATE TABLE diet_plan_shares(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    plan_id INTEGER NOT NULL,
                    version_no INTEGER,
                    channel TEXT NOT NULL,
                    recipient TEXT,
                    status TEXT NOT NULL,
                    provider_message_id TEXT,
                    error TEXT,
                    created_by INTEGER,
                    created_at TEXT,
                    FOREIGN KEY(plan_id) REFERENCES diet_plans(id)
                );
CREATE TABLE diet_plan_versions(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    plan_id INTEGER NOT NULL,
                    version_no INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    goal TEXT,
                    diet_type TEXT,
                    trainer_id INTEGER,
                    start_date TEXT,
                    end_date TEXT,
                    review_date TEXT,
                    instructions TEXT,
                    change_note TEXT,
                    created_by INTEGER,
                    created_at TEXT,
                    UNIQUE(plan_id, version_no),
                    FOREIGN KEY(plan_id) REFERENCES diet_plans(id)
                );
CREATE TABLE diet_plans(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    member_id INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    goal TEXT,
                    diet_type TEXT,
                    trainer_id INTEGER,
                    start_date TEXT,
                    end_date TEXT,
                    review_date TEXT,
                    status TEXT NOT NULL DEFAULT 'Draft',
                    current_version INTEGER NOT NULL DEFAULT 1,
                    source_type TEXT NOT NULL DEFAULT 'scratch',
                    source_ref TEXT,
                    created_by INTEGER,
                    created_at TEXT,
                    updated_at TEXT,
                    FOREIGN KEY(member_id) REFERENCES members(id),
                    FOREIGN KEY(trainer_id) REFERENCES trainers(id)
                );
CREATE TABLE diet_templates(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    goal TEXT,
                    diet_type TEXT,
                    weeks INTEGER,
                    instructions TEXT,
                    source_starter_key TEXT,
                    is_archived INTEGER NOT NULL DEFAULT 0,
                    created_by INTEGER,
                    created_at TEXT,
                    updated_at TEXT
                );
CREATE TABLE enquiries(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                enquiry_code TEXT UNIQUE NOT NULL,
                enquiry_date TEXT NOT NULL,
                full_name TEXT NOT NULL,
                phone TEXT NOT NULL,
                phone_normalized TEXT,
                email TEXT,
                gender TEXT,
                age INTEGER,
                source TEXT NOT NULL DEFAULT 'Walk-in',
                interested_plan_id INTEGER,
                fitness_goal TEXT,
                priority TEXT NOT NULL DEFAULT 'Warm',
                status TEXT NOT NULL DEFAULT 'New',
                assigned_to INTEGER,
                next_follow_up_date TEXT,
                notes TEXT,
                lost_reason TEXT,
                converted_member_id INTEGER,
                converted_at TEXT,
                is_archived INTEGER NOT NULL DEFAULT 0,
                created_by INTEGER,
                created_at TEXT,
                updated_at TEXT,
                FOREIGN KEY(interested_plan_id) REFERENCES membership_plans(id),
                FOREIGN KEY(assigned_to) REFERENCES users(id),
                FOREIGN KEY(converted_member_id) REFERENCES members(id),
                FOREIGN KEY(created_by) REFERENCES users(id)
            );
CREATE TABLE enquiry_followups(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                enquiry_id INTEGER NOT NULL,
                followup_date TEXT NOT NULL,
                mode TEXT NOT NULL,
                notes TEXT,
                status_before TEXT,
                status_after TEXT,
                next_follow_up_date TEXT,
                created_by INTEGER,
                created_at TEXT,
                FOREIGN KEY(enquiry_id) REFERENCES enquiries(id),
                FOREIGN KEY(created_by) REFERENCES users(id)
            );
CREATE TABLE expenses(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                expense_date TEXT NOT NULL,
                category TEXT NOT NULL,
                expense_title TEXT NOT NULL,
                amount REAL NOT NULL,
                payment_mode TEXT NOT NULL,
                reference_no TEXT,
                paid_to TEXT,
                remarks TEXT,
                status TEXT NOT NULL DEFAULT 'Paid',
                created_at TEXT,
                updated_at TEXT
            );
CREATE TABLE member_profiles(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                member_id INTEGER NOT NULL UNIQUE,
                current_weight_kg REAL,
                height_cm REAL,
                target_weight_kg REAL,
                fitness_goal TEXT,
                activity_level TEXT,
                workout_days_per_week INTEGER,
                target_date TEXT,
                diet_type TEXT,
                food_allergies TEXT,
                food_preferences TEXT,
                preferred_cuisine TEXT,
                meals_per_day INTEGER,
                workout_time TEXT,
                wake_up_time TEXT,
                sleep_time TEXT,
                daily_routine TEXT,
                created_at TEXT,
                updated_at TEXT,
                FOREIGN KEY(member_id) REFERENCES members(id)
            );
CREATE TABLE members(

            id INTEGER PRIMARY KEY AUTOINCREMENT,

            member_code TEXT UNIQUE NOT NULL,

            full_name TEXT NOT NULL,

            phone TEXT UNIQUE NOT NULL,

            gender TEXT NOT NULL,

            age INTEGER NOT NULL,

            date_of_birth TEXT,

            address TEXT,

            emergency_contact TEXT,

            biometric_id TEXT UNIQUE,

            status TEXT NOT NULL,

            created_at TEXT,

            updated_at TEXT
        , email TEXT, is_archived INTEGER NOT NULL DEFAULT 0);
CREATE TABLE membership_plans(

            id INTEGER PRIMARY KEY AUTOINCREMENT,

            plan_name TEXT UNIQUE NOT NULL,

            duration_days INTEGER NOT NULL,

            original_fee REAL NOT NULL,

            description TEXT,

            status TEXT NOT NULL,

            created_at TEXT,

            updated_at TEXT
        , is_personal_training
            INTEGER NOT NULL DEFAULT 0, trainer_share_percent
            REAL NOT NULL DEFAULT 0, discount_percent REAL NOT NULL DEFAULT 0, gst_inclusive INTEGER NOT NULL DEFAULT 0);
CREATE TABLE membership_transactions(

            id INTEGER PRIMARY KEY AUTOINCREMENT,

            receipt_no TEXT UNIQUE NOT NULL,

            member_id INTEGER NOT NULL,

            plan_id INTEGER NOT NULL,

            transaction_type TEXT NOT NULL,

            original_fee REAL NOT NULL,

            amount_paid REAL NOT NULL,

            discount REAL NOT NULL,

            remaining_amount REAL NOT NULL,

            payment_status TEXT NOT NULL,

            joining_date TEXT NOT NULL,

            expiry_date TEXT NOT NULL,

            payment_date TEXT NOT NULL,

            remarks TEXT,

            status TEXT NOT NULL,

            created_at TEXT,

            updated_at TEXT, trainer_id INTEGER, trainer_share
            REAL NOT NULL DEFAULT 0, gym_share
            REAL NOT NULL DEFAULT 0, payment_mode TEXT, reference_no TEXT, is_gst_applied INTEGER NOT NULL DEFAULT 0, gst_percent REAL NOT NULL DEFAULT 0, gst_amount REAL NOT NULL DEFAULT 0,

            FOREIGN KEY(member_id)
                REFERENCES members(id),

            FOREIGN KEY(plan_id)
                REFERENCES membership_plans(id)
        );
CREATE TABLE notification_settings (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                retention_days INTEGER
            );
CREATE TABLE notification_states (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                notification_key TEXT NOT NULL UNIQUE,
                priority TEXT NOT NULL,
                category TEXT NOT NULL,
                details TEXT NOT NULL,
                action TEXT,
                status TEXT NOT NULL DEFAULT 'Dismissed',
                reason TEXT,
                resolved_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
CREATE TABLE payroll_settlements(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                staff_id INTEGER NOT NULL,
                salary_record_id INTEGER,
                trainer_id INTEGER,
                salary_month TEXT NOT NULL,
                salary_amount REAL NOT NULL DEFAULT 0,
                pt_commission_amount REAL NOT NULL DEFAULT 0,
                total_amount REAL NOT NULL DEFAULT 0,
                payment_date TEXT NOT NULL,
                payment_mode TEXT NOT NULL,
                reference_no TEXT,
                remarks TEXT,
                status TEXT NOT NULL DEFAULT 'Paid',
                created_at TEXT,
                updated_at TEXT,
                FOREIGN KEY(staff_id) REFERENCES staff(id),
                FOREIGN KEY(salary_record_id) REFERENCES salary_records(id),
                FOREIGN KEY(trainer_id) REFERENCES trainers(id)
            );
CREATE TABLE role_permissions (
            role TEXT NOT NULL,
            module TEXT NOT NULL,
            can_view INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (role, module)
        );
CREATE TABLE salary_advances(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            staff_id INTEGER NOT NULL,
            advance_date TEXT NOT NULL,
            amount REAL NOT NULL,
            payment_mode TEXT NOT NULL,
            reference_no TEXT,
            remarks TEXT,
            created_at TEXT,
            updated_at TEXT,
            FOREIGN KEY(staff_id) REFERENCES staff(id)
        );
CREATE TABLE salary_payments(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            salary_record_id INTEGER NOT NULL,
            payment_date TEXT NOT NULL,
            amount REAL NOT NULL,
            payment_mode TEXT NOT NULL,
            reference_no TEXT,
            remarks TEXT,
            created_at TEXT,
            updated_at TEXT,
            FOREIGN KEY(salary_record_id) REFERENCES salary_records(id)
        );
CREATE TABLE salary_records(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            staff_id INTEGER NOT NULL,
            salary_month TEXT NOT NULL,
            basic_salary REAL NOT NULL DEFAULT 0,
            calendar_days INTEGER NOT NULL DEFAULT 0,
            working_days INTEGER NOT NULL DEFAULT 0,
            present_days REAL NOT NULL DEFAULT 0,
            half_days REAL NOT NULL DEFAULT 0,
            paid_leave_days REAL NOT NULL DEFAULT 0,
            absent_days REAL NOT NULL DEFAULT 0,
            earned_salary REAL NOT NULL DEFAULT 0,
            advance_amount REAL NOT NULL DEFAULT 0,
            deduction_amount REAL NOT NULL DEFAULT 0,
            net_salary REAL NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'Draft',
            created_at TEXT,
            updated_at TEXT,
            FOREIGN KEY(staff_id) REFERENCES staff(id),
            UNIQUE(staff_id, salary_month)
        );
CREATE TABLE staff(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            employee_code TEXT UNIQUE NOT NULL,
            full_name TEXT NOT NULL,
            phone TEXT UNIQUE NOT NULL,
            gender TEXT NOT NULL,
            date_of_birth TEXT,
            address TEXT,
            emergency_contact TEXT,
            designation TEXT NOT NULL,
            department TEXT,
            joining_date TEXT NOT NULL,
            monthly_salary REAL NOT NULL DEFAULT 0,
            salary_basis TEXT NOT NULL DEFAULT 'Calendar Days',
            status TEXT NOT NULL DEFAULT 'Active',
            created_at TEXT,
            updated_at TEXT
        , photo_path TEXT, trainer_id INTEGER);
CREATE TABLE staff_attendance(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            staff_id INTEGER NOT NULL,
            attendance_date TEXT NOT NULL,
            status TEXT NOT NULL,
            check_in TEXT,
            check_out TEXT,
            remarks TEXT,
            created_at TEXT,
            updated_at TEXT,
            FOREIGN KEY(staff_id) REFERENCES staff(id),
            UNIQUE(staff_id, attendance_date)
        );
CREATE TABLE trainer_members(

            id INTEGER PRIMARY KEY AUTOINCREMENT,

            trainer_id INTEGER NOT NULL,

            member_id INTEGER NOT NULL,

            assigned_date TEXT NOT NULL,

            ended_date TEXT,

            status TEXT NOT NULL,

            created_at TEXT,

            updated_at TEXT,

            FOREIGN KEY(trainer_id)
                REFERENCES trainers(id),

            FOREIGN KEY(member_id)
                REFERENCES members(id),

            UNIQUE(trainer_id, member_id)
        );
CREATE TABLE trainer_payouts(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trainer_id INTEGER NOT NULL,
                payout_date TEXT NOT NULL,
                amount REAL NOT NULL DEFAULT 0,
                payment_mode TEXT NOT NULL,
                reference_no TEXT,
                remarks TEXT,
                status TEXT NOT NULL DEFAULT 'Paid',
                created_at TEXT,
                updated_at TEXT, earning_month TEXT,
                FOREIGN KEY(trainer_id) REFERENCES trainers(id)
            );
CREATE TABLE trainer_sessions(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trainer_id INTEGER NOT NULL,
                member_id INTEGER,
                session_date TEXT NOT NULL,
                start_time TEXT NOT NULL,
                end_time TEXT,
                session_type TEXT,
                status TEXT NOT NULL DEFAULT 'Scheduled',
                notes TEXT,
                created_at TEXT,
                updated_at TEXT,
                FOREIGN KEY(trainer_id) REFERENCES trainers(id),
                FOREIGN KEY(member_id) REFERENCES members(id)
            );
CREATE TABLE trainers(

            id INTEGER PRIMARY KEY AUTOINCREMENT,

            trainer_code TEXT UNIQUE NOT NULL,

            full_name TEXT NOT NULL,

            phone TEXT UNIQUE NOT NULL,

            gender TEXT NOT NULL,

            specialization TEXT,

            joining_date TEXT,

            address TEXT,

            emergency_contact TEXT,

            status TEXT NOT NULL,

            created_at TEXT,

            updated_at TEXT
        , email TEXT, employee_code TEXT);
CREATE TABLE users(

            id INTEGER PRIMARY KEY AUTOINCREMENT,

            username TEXT UNIQUE NOT NULL,

            password TEXT NOT NULL,

            role TEXT NOT NULL,

            status TEXT NOT NULL,

            created_at TEXT,

            updated_at TEXT
        );
CREATE TABLE whatsapp_messages(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                message_type TEXT NOT NULL,
                document_type TEXT NOT NULL,
                document_reference TEXT NOT NULL,
                recipient_number TEXT NOT NULL,
                provider TEXT,
                status TEXT NOT NULL DEFAULT 'PENDING',
                provider_message_id TEXT,
                error_message TEXT,
                created_at TEXT NOT NULL,
                sent_at TEXT,
                delivered_at TEXT
            );
