       IDENTIFICATION DIVISION.
       PROGRAM-ID. CALCULATOR.

       DATA DIVISION.
       WORKING-STORAGE SECTION.
       01  NUM1             PIC S9(9)V99.
       01  NUM2             PIC S9(9)V99.
       01  RESULT           PIC S9(9)V99.
       01  OPERATOR         PIC X.
       01  CONTINUE-FLAG    PIC X VALUE 'T'.
       01  DIV-BY-ZERO-MSG  PIC X(28)
           VALUE 'Blad: dzielenie przez zero.'.

       PROCEDURE DIVISION.
       MAIN-PARAGRAPH.
           DISPLAY '=== Kalkulator COBOL ==='.

           PERFORM UNTIL CONTINUE-FLAG = 'N'
               DISPLAY 'Podaj pierwsza liczbe:'
               ACCEPT NUM1

               DISPLAY 'Podaj operator (+, -, *, /):'
               ACCEPT OPERATOR

               DISPLAY 'Podaj druga liczbe:'
               ACCEPT NUM2

               EVALUATE OPERATOR
                   WHEN '+'
                       COMPUTE RESULT = NUM1 + NUM2
                       DISPLAY 'Wynik: ' RESULT
                   WHEN '-'
                       COMPUTE RESULT = NUM1 - NUM2
                       DISPLAY 'Wynik: ' RESULT
                   WHEN '*'
                       COMPUTE RESULT = NUM1 * NUM2
                       DISPLAY 'Wynik: ' RESULT
                   WHEN '/'
                       IF NUM2 = 0
                           DISPLAY DIV-BY-ZERO-MSG
                       ELSE
                           COMPUTE RESULT = NUM1 / NUM2
                           DISPLAY 'Wynik: ' RESULT
                       END-IF
                   WHEN OTHER
                       DISPLAY 'Nieznany operator.'
               END-EVALUATE

               DISPLAY 'Czy chcesz kontynuowac? (T/N):'
               ACCEPT CONTINUE-FLAG
               MOVE FUNCTION UPPER-CASE(CONTINUE-FLAG) TO CONTINUE-FLAG
           END-PERFORM

           DISPLAY 'Koniec programu.'
           STOP RUN.
