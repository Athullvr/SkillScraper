export function Checkout() {
  return (
    <main>
      <form aria-label="Checkout">
        <label>
          Email
          <input name="email" type="email" />
        </label>
        <button type="submit">Continue</button>
      </form>
      <div role="dialog" aria-modal="true" aria-labelledby="confirm-title">
        <h2 id="confirm-title">Confirm order</h2>
        <button type="button">Close</button>
      </div>
    </main>
  );
}
